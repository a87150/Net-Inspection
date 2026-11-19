import pathlib
for path, name in [
    ("tests/people/test_directory_ui.py", "test_provider_tabs_are_import_only_and_credentials_are_write_only"),
    ("tests/common/test_table_filters.py", "test_workspace_keeps_accessible_filter_table_summary_and_pagination_order"),
    ("tests/inspections/test_runtime_review.py", "test_row_execution_can_restore_exactly_one_selected_asset"),
]:
    lines = pathlib.Path(path).read_text(encoding="utf-8").splitlines()
    start = next(i for i, l in enumerate(lines) if name in l)
    print("===", path, "line", start+1)
    for l in lines[start:start+26]:
        print(l[:150])