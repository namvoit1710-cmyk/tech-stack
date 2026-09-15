"""Template-driven AutoForm schema generation.

Builds the Template Data Editor node's AutoForm schema from a governance
template's field configuration, binding value-help columns to Integration Hub
search-help endpoints. See ``docs/template-data-editor-node.md``.
"""

from app.layer1_domain.template_schema.builder import (
    build_template_data_edit_schema,
)
from app.layer1_domain.template_schema.models import TemplateFieldConfig

__all__ = ["TemplateFieldConfig", "build_template_data_edit_schema"]
