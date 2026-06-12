# 批量操作跨工作区校验修复

## 问题描述

批量编辑功能在处理 `set_collection`（修改收藏夹）和 `set_tags`（修改标签）时，没有验证前端提交的 PDF 列表是否都属于当前工作区。这导致以下安全风险：

1. **工作区切换后残留旧选择**：用户在多个工作区之间切换后，如果前端缓存了旧工作区的 PDF 选择，批量操作可能会影响其他工作区的 PDF。

2. **恶意混合请求**：攻击者可以构造请求，将不同工作区的 PDF ID 混在一起，强制将其他工作区的 PDF 移动到当前工作区的收藏夹，或为其添加标签。

## 根本原因

`BulkActions.post()` 方法使用 `PdfMixin.get_object()` 来获取 PDF 对象，该方法内部调用 `user_profile.all_pdfs.get(id=pdf_id)`。而 `all_pdfs` 属性返回的是用户**所有工作区**的 PDF，不仅仅是当前工作区的 PDF。

对于 `archive`、`delete` 和 `star` 操作，这种设计是可以接受的，因为这些操作不涉及工作区特定的副作用。但对于 `set_collection` 和 `set_tags`，这会导致跨工作区污染：

- `set_collection`：可能将其他工作区的 PDF 移动到当前工作区的收藏夹
- `set_tags`：使用 `pdfs[0].collection.workspace` 来处理标签，如果该 PDF 来自其他工作区，会创建错误的标签关联

## 修复方案

### 1. 添加工作区校验方法

在 `pdfding/pdf/views/pdf_bulk_action_views.py` 中为 `BulkActions` 类添加了一个新的静态方法：

```python
@staticmethod
def _validate_pdfs_belong_to_workspace(pdfs: list[Pdf], workspace: Workspace) -> None:
    """Validate that all PDFs belong to the specified workspace. Raise Http404 if not."""

    for pdf in pdfs:
        if pdf.collection.workspace_id != workspace.id:
            raise Http404('One or more PDFs do not belong to the current workspace!')
```

### 2. 在批量操作中应用校验

修改了 `post()` 方法的 `set_collection` 和 `set_tags` 分支，在执行操作前先调用校验方法：

```python
case 'set_collection':
    current_workspace = request.user.profile.current_workspace
    self._validate_pdfs_belong_to_workspace(pdfs, current_workspace)
    collection_id = request.POST.get('collection_id')
    self.set_collection(pdfs, current_workspace, collection_id)
case 'set_tags':
    current_workspace = request.user.profile.current_workspace
    self._validate_pdfs_belong_to_workspace(pdfs, current_workspace)
    tag_string = request.POST.get('tag_string')
    self.set_tags(pdfs, tag_string, request)
```

### 3. 保持向后兼容

`archive`、`delete` 和 `star` 操作**不需要**工作区校验，因为它们是 PDF 级别的操作，不涉及工作区特定的资源（如收藏夹或标签）。这些操作继续按原方式工作，确保向后兼容。

## 回归测试

在 `pdfding/pdf/tests/test_views/test_pdf_bulk_actions_views.py` 中添加了 7 个回归测试：

### 校验失败的测试（4 个）

1. **test_set_collection_cross_workspace_rejected**
   - 验证：当请求包含本地和外部 PDF 时，`set_collection` 返回 404
   - 验证：本地和外部 PDF 都没有被移动

2. **test_set_collection_only_foreign_pdf_rejected**
   - 验证：当请求只包含外部 PDF 时，`set_collection` 返回 404
   - 验证：外部 PDF 没有被移动

3. **test_set_tags_cross_workspace_rejected**
   - 验证：当请求包含本地和外部 PDF 时，`set_tags` 返回 404
   - 验证：本地和外部 PDF 的标签都没有被修改

4. **test_set_tags_only_foreign_pdf_rejected**
   - 验证：当请求只包含外部 PDF 时，`set_tags` 返回 404
   - 验证：外部 PDF 没有被添加标签

### 向后兼容的测试（3 个）

5. **test_archive_cross_workspace_still_works**
   - 验证：`archive` 操作可以跨工作区工作
   - 验证：本地和外部 PDF 都被成功归档

6. **test_delete_cross_workspace_still_works**
   - 验证：`delete` 操作可以跨工作区工作
   - 验证：本地和外部 PDF 都被成功删除

7. **test_star_cross_workspace_still_works**
   - 验证：`star` 操作可以跨工作区工作
   - 验证：本地和外部 PDF 都被成功加星

## 测试结果

```
17 passed in 6.95s (test_pdf_bulk_actions_views.py)
283 passed in 40.28s (pdf/tests/)
```

所有测试通过，包括：
- 原有的 10 个批量操作测试
- 新增的 7 个回归测试
- PDF 模块的其他 266 个测试

## 影响范围

- **修改文件**：`pdfding/pdf/views/pdf_bulk_action_views.py`
- **测试文件**：`pdfding/pdf/tests/test_views/test_pdf_bulk_actions_views.py`
- **影响功能**：批量修改收藏夹、批量修改标签
- **不影响功能**：批量归档、批量删除、批量加星

## 安全加固

此修复防止了以下攻击场景：

1. **会话混淆攻击**：用户在多个工作区之间快速切换时，前端可能缓存旧的工作区选择，导致意外的跨工作区操作。

2. **恶意请求攻击**：攻击者构造恶意请求，将其他工作区的 PDF ID 混入批量操作，试图：
   - 将其他工作区的 PDF 移动到当前工作区（通过 `set_collection`）
   - 为其他工作区的 PDF 添加当前工作区的标签（通过 `set_tags`）

现在这两种操作都会严格校验每个 PDF 的归属，确保操作仅限于当前工作区。
