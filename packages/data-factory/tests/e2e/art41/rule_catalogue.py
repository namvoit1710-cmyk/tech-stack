"""All 18 rules assigned to ART41.00, mapped onto the workbook that carries them.

The rule trace names fields technically (``PURCHASING.supplier``); the workbook
names them as labels (``Supplier (String)``). This is that mapping, written out
once, so a change to either side has one place to be fixed.

The *value* mappings -- which supplier belongs to which purchasing organisation,
which valuation class an article type implies -- are not in the trace at all.
The template screen shows the field relationship and not the table behind it.
They are mined instead from ``ART41.00_500_valid_rules.xlsx``: 500 rows that
satisfy every assigned rule, so the distinct (source, target) pairs it contains
are the permitted set by construction. Reading the same pairs off the invalid
workbook then flags exactly the seeded violations, which is the check that says
the mined mapping is the real one rather than an artefact of the sample.
"""

import os

#: Both workbooks live here. Set ART41_WORKBOOK_DIR to point at them.
WORKBOOK_DIR = os.environ.get("ART41_WORKBOOK_DIR", "")
VALID = os.path.join(WORKBOOK_DIR, "ART41.00_500_valid_rules.xlsx")
INVALID = os.path.join(WORKBOOK_DIR, "ART41.00_500_invalid_rules.xlsx")

FILE_SERVICE = os.environ.get(
    "ART41_FILE_SERVICE",
    "https://smdg-ai-tenant-1-file-service.cfapps.br10.hana.ondemand.com")
DATA_FACTORY = os.environ.get(
    "ART41_DATA_FACTORY",
    "https://smdg-ai-tenant-1-data-factory.cfapps.br10.hana.ondemand.com")

ITEM = "ItemID (Integer)"

#: ``kind`` is the engine rule type; "-" means it cannot run as a row rule and
#: the ``why`` says what is missing. Nothing is dropped silently.
RULES = [
    # ---------------------------------------------------------- validation
    dict(id="LENGTH_40", kind="length", sheet="ArticleMaster",
         target="Base Unit (String)", params={"max": 40},
         path="GENERALDATA.baseUnit",
         why="Cannot exceed 40 characters"),

    # -------------------------------------------------------- filter rules
    dict(id="FILTER_CHARC_VALUE", kind="filter", sheet="ARTMasterCharac",
         source="Charc (String)", target="Charc Value (String)",
         source_path="CHARACTERISTIC.charc", path="CHARACTERISTIC.charcValue",
         why="characteristic value must suit the characteristic"),
    dict(id="FILTER_CLASS_CHARC", kind="filter", sheet="ARTMasterCharac",
         source="Class (String)", target="Charc (String)",
         source_path="CLASSIFICATION.class", path="CHARACTERISTIC.charc",
         why="class decides the valid characteristics"),
    dict(id="FILTER_CLASSTYPE_CLASS", kind="filter", sheet="ARTMasterClass",
         source="Class Type (String)", target="Class (String)",
         source_path="CLASSIFICATION.classType", path="CLASSIFICATION.class",
         why="class type limits the class list"),
    dict(id="FILTER_COUNTRY_REGION", kind="filter", sheet="ArticleMaster",
         source="Country Of Origin (String)", target="Region Of Origin (String)",
         source_path="GENERALDATA.countryOfOrigin", path="GENERALDATA.regionOfOrigin",
         why="region must belong to the country"),
    dict(id="FILTER_PURCHORG_SUPPLIER", kind="filter", sheet="ARTMasterPurchasing",
         source="Purchasing Organization (String)", target="Supplier (String)",
         source_path="PURCHASING.purchasingOrganization", path="PURCHASING.supplier",
         why="supplier must serve the purchasing organisation"),
    dict(id="FILTER_SALESPRICE_SUPPLIER", kind="filter", sheet="ARTMasterSalesPrice",
         source="Purchasing Org. (String)", target="Supplier (String)",
         source_path="SALESPRICE.purchasingOrganization", path="SALESPRICE.supplier",
         why="same, in the sales-price context"),

    # ---------------------------------------------------- assignment rules
    dict(id="ASSIGN_VALUATION_CLASS", kind="assignment", sheet="ArticleMaster",
         source="Article Type (String)", target="Valuation Class (String)",
         source_path="ARTICLE.articleType", path="GENERALDATA.valuationClass",
         why="article type decides valuation class"),
    dict(id="ASSIGN_VALUATION_CLASS_VAL", kind="assignment", sheet="ARTMasterValuation",
         source="Article Type (String)", target="Valuation Class (String)",
         source_path="ARTICLE.articleType", path="VALUATION.valuationClass",
         join_from="ArticleMaster",
         why="same rule, target on the Valuation table"),
    dict(id="ASSIGN_OVERDELIVERY", kind="assignment", sheet="ArticleMaster",
         source="Purchasing value key (String)", target="Overdeliv. Tolerance (Decimal)",
         source_path="ARTMASTERPURVALUE.purchasingValueKey",
         path="ARTMASTERPURVALUE.overdeliveryTolerance",
         why="purchasing value key decides the tolerance"),
    dict(id="ASSIGN_REMINDER_1", kind="assignment", sheet="ArticleMaster",
         source="Purchasing value key (String)", target="1st Reminder/Exped. (Decimal)",
         source_path="ARTMASTERPURVALUE.purchasingValueKey",
         path="ARTMASTERPURVALUE.reminderDays1st", why="first reminder days"),
    dict(id="ASSIGN_REMINDER_2", kind="assignment", sheet="ArticleMaster",
         source="Purchasing value key (String)", target="2nd Reminder/Exped. (Decimal)",
         source_path="ARTMASTERPURVALUE.purchasingValueKey",
         path="ARTMASTERPURVALUE.reminderDays2nd", why="second reminder days"),
    dict(id="ASSIGN_REMINDER_3", kind="assignment", sheet="ArticleMaster",
         source="Purchasing value key (String)", target="3rd Reminder/Exped. (Decimal)",
         source_path="ARTMASTERPURVALUE.purchasingValueKey",
         path="ARTMASTERPURVALUE.reminderDays3rd", why="third reminder days"),
    dict(id="ASSIGN_UNDERDELIVERY", kind="-", sheet="ArticleMaster",
         source="Purchasing value key (String)", target=None,
         path="ARTMASTERPURVALUE.underdeliveryTolerance",
         why="the workbook has no underdelivery tolerance column"),
    dict(id="ASSIGN_EFFECTIVE_PRICE", kind="assignment", sheet="ARTMasterPurchasing",
         source="Net Price (Decimal)", target="Effective Price (Decimal)",
         source_path="CONDITIONS.netPriceAmount", path="CONDITIONS.effectivePrice",
         why="net price decides the effective price"),
    dict(id="ASSIGN_DIST_CHANNEL", kind="assignment", sheet="ARTMasterSD",
         source="SalesOrg (String)", target="DistributionChnl (String)",
         source_path="SALES.articleSalesOrg", path="SALES.articleDistributionChnl",
         why="sales org decides distribution channel"),

    # ---------------------------------------------------------- derivation
    dict(id="ARTM_FOOD", kind="derive", sheet="ARTMasterPurchasing",
         source="Merchandise Category (String)", target="Purchasing Info Record (String)",
         join_from="ArticleMaster", when_value="FOOD", then_value="PC",
         source_path="ARTICLE.merchandiseCategory", path="PURCHASING.purchasingInfoRecord",
         why="FOOD articles derive a PC info record -- condition on another sheet"),

    # ------------------------------------------------------------- ui only
    dict(id="INVISIBLE_BASICTEXT", kind="-", sheet=None, target=None,
         path="BASICTEXT", why="section visibility, not a row rule"),
]


def permitted_pairs(reader, rule):
    """The (source, target) pairs the valid workbook says are allowed.

    A rule whose source lives on another sheet is joined on ItemID first, which
    is how a cross-table assignment gets a mapping at all.
    """
    import polars as pl

    source, target, sheet = rule["source"], rule["target"], rule["sheet"]
    if rule.get("join_from"):
        left = reader.load_sheet(rule["join_from"], header_row=2).to_polars()
        right = reader.load_sheet(sheet, header_row=2).to_polars()
        frame = left.select([ITEM, source]).join(
            right.select([ITEM, target]), on=ITEM, how="inner")
    else:
        frame = reader.load_sheet(sheet, header_row=2).to_polars()

    if source not in frame.columns or target not in frame.columns:
        return None
    return (frame.select([pl.col(source).cast(pl.Utf8),
                          pl.col(target).cast(pl.Utf8)])
            .drop_nulls().unique().sort(source))
