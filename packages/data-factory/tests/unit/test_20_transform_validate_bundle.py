"""The bundle use case: one multi-table source file, table by table, in order.

It owns only orchestration -- ordering, naming, wiring one table's output into a
later table's rule, and reporting. The transform and validation use cases it
composes are exercised elsewhere; here they are doubles, so a failure points at
the loop rather than at Polars.
"""

import pytest

from app.layer2_application.features.bundle.use_cases.transform_validate_bundle_usecase import (
    BundleTableSpec,
    TransformValidateBundleCommand,
    TransformValidateBundleUseCase,
)


class _Logger:
    def __init__(self):
        self.warnings = []

    def info(self, *args, **kwargs): pass
    def debug(self, *args, **kwargs): pass
    def error(self, *args, **kwargs): pass

    def warning(self, message, *args, **kwargs):
        self.warnings.append(str(message))


class _TransformResultStub:
    def __init__(self, file_id, rows=1, columns=1):
        self.output_file_id = file_id
        self.output_version_id = f"{file_id}-v1"
        self.total_rows = rows
        self.total_columns = columns
        self.stale_version = False
        self.current_version_id = None


class _TransformOutcome:
    def __init__(self, success, result=None, message=""):
        self.success = success
        self.result = result
        self.message = message


class _FakeTransformUseCase:
    """Records the commands it receives; mints a file id per sheet."""

    def __init__(self, failing_sheets=()):
        self.commands = []
        self.failing_sheets = set(failing_sheets)

    async def execute(self, command):
        self.commands.append(command)
        sheet = (command.sheet_names or ["<none>"])[0]
        if sheet in self.failing_sheets:
            return _TransformOutcome(False, message=f"boom on {sheet}")
        return _TransformOutcome(
            True, _TransformResultStub(f"file-{sheet}", rows=3), message="ok"
        )


class _ODataStub:
    def __init__(self, file_id):
        self.result_file_id = file_id
        self.result_file_url = f"https://files/{file_id}"


class _ValidationResultStub:
    def __init__(self, total, invalid):
        self.total_rows = total
        self.valid_rows = total - invalid
        self.invalid_rows = invalid
        self.odata = _ODataStub("result-file")


class _ValidationOutcome:
    def __init__(self, success, result=None, message=""):
        self.success = success
        self.result = result
        self.message = message


class _FakeValidationUseCase:
    def __init__(self, invalid_rows=0, succeed=True):
        self.commands = []
        self.invalid_rows = invalid_rows
        self.succeed = succeed

    async def execute(self, command):
        self.commands.append(command)
        if not self.succeed:
            return _ValidationOutcome(False, message="validation blew up")
        return _ValidationOutcome(
            True, _ValidationResultStub(3, self.invalid_rows), message="validated"
        )


def _usecase(transform=None, validation=None):
    logger = _Logger()
    transform = transform or _FakeTransformUseCase()
    validation = validation or _FakeValidationUseCase()
    return (
        TransformValidateBundleUseCase(
            logger=logger,
            schema_transform_usecase=transform,
            data_validation_usecase=validation,
        ),
        transform,
        validation,
        logger,
    )


def _table(name, sheet=None, rules=None, **kwargs):
    return BundleTableSpec(
        name=name, sheet_names=[sheet or name], rules=rules or [], **kwargs
    )


@pytest.mark.asyncio
async def test_a_table_can_pin_a_label_the_rest_of_the_bundle_spells_differently():
    """One label, two sections. `Supplier (String)` is PURCHASING.supplier on the
    purchasing sheet and SALESPRICE.supplier on the sales-price sheet, so a
    single bundle-wide map cannot express both and the later table would quietly
    win. The table-level map is merged over the bundle-level one."""
    usecase, transform, _, _ = _usecase()

    await usecase.execute(
        TransformValidateBundleCommand(
            source_file_id="wb-1",
            field_path_overrides={"Supplier (String)": "PURCHASING.supplier",
                                  "Article (String)": "ARTICLE.article"},
            tables=[
                _table("ARTMasterPurchasing"),
                _table("ARTMasterSalesPrice",
                       field_path_overrides={"Supplier (String)": "SALESPRICE.supplier"}),
            ],
        )
    )

    purchasing, salesprice = transform.commands
    assert purchasing.field_path_overrides["Supplier (String)"] == "PURCHASING.supplier"
    assert salesprice.field_path_overrides["Supplier (String)"] == "SALESPRICE.supplier"
    # the bundle-wide entries survive on both
    assert salesprice.field_path_overrides["Article (String)"] == "ARTICLE.article"


@pytest.mark.asyncio
async def test_paths_are_derived_for_a_bundle_without_being_asked():
    """A bundle is reading a mass-upload template, which is the shape that has a
    banner to read, so the paths come for free rather than on request."""
    usecase, transform, _, _ = _usecase()
    await usecase.execute(
        TransformValidateBundleCommand(source_file_id="wb-1", tables=[_table("A")])
    )
    assert transform.commands[0].derive_field_paths is True


@pytest.mark.asyncio
async def test_each_table_is_transformed_then_validated():
    usecase, transform, validation, _ = _usecase()

    result = await usecase.execute(
        TransformValidateBundleCommand(
            source_file_id="wb-1",
            default_header_row=2,
            tables=[
                _table("ArticleMaster", validation_rules=[{"rule_name": "LENGTH_40"}]),
                _table("ARTMasterPurchasing", validation_rules=[{"rule_name": "REQ"}]),
            ],
        )
    )

    assert result.success is True
    assert [t.name for t in result.tables] == ["ArticleMaster", "ARTMasterPurchasing"]
    assert result.table_file_ids == {
        "ArticleMaster": "file-ArticleMaster",
        "ARTMasterPurchasing": "file-ARTMasterPurchasing",
    }
    # Validation runs against the transform's output, not the workbook.
    assert [c.file_id for c in validation.commands] == [
        "file-ArticleMaster",
        "file-ARTMasterPurchasing",
    ]
    assert all(c.file_format == "csv" for c in validation.commands)
    assert len(transform.commands) == 2


@pytest.mark.asyncio
async def test_default_header_row_applies_unless_the_table_pins_its_own():
    usecase, transform, _, _ = _usecase()

    await usecase.execute(
        TransformValidateBundleCommand(
            source_file_id="wb-1",
            default_header_row=2,
            tables=[_table("A"), _table("B", header_row=5)],
        )
    )

    assert [c.header_row for c in transform.commands] == [2, 5]


@pytest.mark.asyncio
async def test_a_rule_can_name_an_earlier_table_instead_of_a_file_id():
    """The caller cannot know the file ids a run will mint, so rules name tables."""
    usecase, transform, _, _ = _usecase()

    await usecase.execute(
        TransformValidateBundleCommand(
            source_file_id="wb-1",
            tables=[
                _table("ArticleMaster"),
                _table(
                    "ARTMasterPurchasing",
                    depends_on=["ArticleMaster"],
                    rules=[
                        {
                            "type": "join_reference",
                            "params": {
                                "table": "ArticleMaster",
                                "on": ["itemID"],
                                "columns": ["articleGroup"],
                            },
                        }
                    ],
                ),
            ],
        )
    )

    params = transform.commands[1].rules[0]["params"]
    assert params["file_id"] == "file-ArticleMaster"
    assert params["file_format"] == "csv"
    assert params["version_id"] == "file-ArticleMaster-v1"
    assert "table" not in params
    # The unresolved rule the caller handed in must not be mutated.
    assert params is not transform.commands[1].rules[0].get("__original__")


@pytest.mark.asyncio
async def test_rules_without_a_table_reference_pass_through_untouched():
    usecase, transform, _, _ = _usecase()
    rule = {"type": "rename_columns", "params": {"mapping": {"a": "b"}}}

    await usecase.execute(
        TransformValidateBundleCommand(source_file_id="wb-1", tables=[_table("A", rules=[rule])])
    )

    assert transform.commands[0].rules == [rule]


@pytest.mark.asyncio
async def test_an_unmet_dependency_fails_that_table_with_a_clear_message():
    usecase, _, _, logger = _usecase()

    result = await usecase.execute(
        TransformValidateBundleCommand(
            source_file_id="wb-1",
            tables=[_table("Child", depends_on=["Parent"]), _table("Parent")],
        )
    )

    assert result.success is False
    child = next(t for t in result.tables if t.name == "Child")
    assert child.success is False
    assert "depends on ['Parent']" in child.message
    # The independent table still runs.
    assert next(t for t in result.tables if t.name == "Parent").success is True


@pytest.mark.asyncio
async def test_one_failing_table_does_not_stop_the_rest_by_default():
    usecase, _, _, _ = _usecase(transform=_FakeTransformUseCase(failing_sheets=["B"]))

    result = await usecase.execute(
        TransformValidateBundleCommand(
            source_file_id="wb-1", tables=[_table("A"), _table("B"), _table("C")]
        )
    )

    assert [t.success for t in result.tables] == [True, False, True]
    assert result.success is False
    assert "Failed: ['B']" in result.message


@pytest.mark.asyncio
async def test_fail_fast_stops_and_says_how_much_was_skipped():
    """A short-circuited run must never read as full coverage."""
    usecase, _, _, _ = _usecase(transform=_FakeTransformUseCase(failing_sheets=["B"]))

    result = await usecase.execute(
        TransformValidateBundleCommand(
            source_file_id="wb-1",
            fail_fast=True,
            tables=[_table("A"), _table("B"), _table("C")],
        )
    )

    assert len(result.tables) == 2
    assert "1 table(s) not attempted" in result.message
    assert result.success is False


@pytest.mark.asyncio
async def test_invalid_rows_are_totalled_across_tables():
    usecase, _, _, _ = _usecase(validation=_FakeValidationUseCase(invalid_rows=2))

    result = await usecase.execute(
        TransformValidateBundleCommand(
            source_file_id="wb-1",
            tables=[
                _table("A", validation_rules=[{"rule_name": "R"}]),
                _table("B", validation_rules=[{"rule_name": "R"}]),
            ],
        )
    )

    assert result.invalid_rows == 4
    assert result.total_rows == 6
    assert result.tables[0].validation["result_file_id"] == "result-file"


@pytest.mark.asyncio
async def test_a_table_without_validation_rules_is_only_transformed():
    usecase, _, validation, _ = _usecase()

    result = await usecase.execute(
        TransformValidateBundleCommand(source_file_id="wb-1", tables=[_table("A")])
    )

    assert result.tables[0].success is True
    assert result.tables[0].validation is None
    assert validation.commands == []


@pytest.mark.asyncio
async def test_a_rule_set_id_is_forwarded_instead_of_inline_rules():
    usecase, _, validation, _ = _usecase()

    await usecase.execute(
        TransformValidateBundleCommand(
            source_file_id="wb-1",
            tables=[_table("A", validation_rule_set_id="ART41.00-ArticleMaster")],
        )
    )

    assert validation.commands[0].rule_set_id == "ART41.00-ArticleMaster"
    assert validation.commands[0].rules is None


@pytest.mark.asyncio
async def test_a_validation_failure_marks_the_table_failed():
    usecase, _, _, _ = _usecase(validation=_FakeValidationUseCase(succeed=False))

    result = await usecase.execute(
        TransformValidateBundleCommand(
            source_file_id="wb-1", tables=[_table("A", validation_rules=[{"rule_name": "R"}])]
        )
    )

    assert result.tables[0].success is False
    assert "validation blew up" in result.tables[0].message


@pytest.mark.asyncio
async def test_duplicate_table_names_are_rejected_up_front():
    """Two tables under one name would silently overwrite each other's file id."""
    usecase, transform, _, _ = _usecase()

    result = await usecase.execute(
        TransformValidateBundleCommand(
            source_file_id="wb-1", tables=[_table("A"), _table("A", sheet="A2")]
        )
    )

    assert result.success is False
    assert "unique" in result.message
    assert transform.commands == []


@pytest.mark.asyncio
async def test_an_empty_bundle_is_rejected():
    usecase, _, _, _ = _usecase()

    result = await usecase.execute(
        TransformValidateBundleCommand(source_file_id="wb-1", tables=[])
    )

    assert result.success is False
    assert "No tables" in result.message


@pytest.mark.asyncio
async def test_validation_is_skipped_loudly_when_the_use_case_is_not_wired():
    logger = _Logger()
    transform = _FakeTransformUseCase()
    usecase = TransformValidateBundleUseCase(
        logger=logger, schema_transform_usecase=transform, data_validation_usecase=None
    )

    result = await usecase.execute(
        TransformValidateBundleCommand(
            source_file_id="wb-1", tables=[_table("A", validation_rules=[{"rule_name": "R"}])]
        )
    )

    assert result.tables[0].success is True
    assert "Validation skipped" in result.tables[0].message
