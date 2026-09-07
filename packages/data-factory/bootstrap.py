from typing import Dict, Any
from app.layer4_frameworks.config.app_config import settings
from app.layer4_frameworks.logger.app_logger import AppLogger
from app.layer4_frameworks.providers.storage.node_storage_provider import NodeFileStorageProvider
from app.layer4_frameworks.providers.validation.polars_validator_provider import PolarsValidatorProvider
from app.layer4_frameworks.providers.transformation.polars_transformer_provider import PolarsTransformerProvider
from app.layer4_frameworks.providers.transformation.polars_row_transformer_provider import PolarsRowTransformerProvider
from app.layer4_frameworks.providers.transformation.polars_schema_transformer_provider import PolarsSchemaTransformerProvider
from app.layer4_frameworks.providers.odata.polars_odata_provider import PolarsODataProvider
from app.layer4_frameworks.repositories.rule_repository import RuleRepository
from app.layer4_frameworks.orm.config.database_config import db_manager
from app.layer4_frameworks.monitoring.performance_decorator import track_performance

from app.layer1_domain.entities.rule_management import RuleSet
import json
import os

async def build_app_container() -> dict:
    print(f"🛠️  Bootstrapping {settings.APP_NAME}...")
    print("🔍 Initializing Infrastructure & Providers (Layer 4)...")
    
    logger = AppLogger()
    storage = NodeFileStorageProvider(settings.FILE_SERVER_URL, logger)
    validator = PolarsValidatorProvider(logger, storage)
    transformer = PolarsTransformerProvider(logger, storage)
    row_transformer = PolarsRowTransformerProvider(logger, storage)
    schema_transformer = PolarsSchemaTransformerProvider(logger, storage)
    odata_parser = PolarsODataProvider()  # <-- New Provider Initiated
    
    rule_repository = RuleRepository(
        session_factory=db_manager.get_session_factory()
    )
    
    print("🔍 Loading Application Features and Injecting Dependencies (Layer 2)...")
    
    container = {
        "logger": logger,
        # Framework-level performance monitor, wired at the composition root so the
        # transport layer's @monitored hook can profile use cases without inner
        # layers importing framework code.
        "performance_monitor": track_performance,
    }
    
    # Load Features Dynamically with Error Handling
    # This allows CI/CD to drop features by deleting folders without breaking bootstrap
    
    # Feature: DataValidationUseCase
    try:
        from app.layer2_application.features.data_validation.use_cases.data_validation_usecase import DataValidationUseCase
        data_validation_usecase = DataValidationUseCase(
            logger=logger, 
            validator=validator, 
            rule_repo=rule_repository,
            storage=storage,          # <-- Storage injected for query/download endpoints
            odata_parser=odata_parser # <-- OData injected for querying
        )
        container["data_validation_usecase"] = data_validation_usecase
        print("  [+] Loaded Feature: DataValidationUseCase")
    except ImportError as e:
        print(f"  [-] Skipped Feature: DataValidationUseCase (ImportError: {e})")
    except Exception as e:
        print(f"  [⚠️ ] Failed to load Feature: DataValidationUseCase (Error: {e})")
    
    # Feature: DataTransformationUseCase
    try:
        from app.layer2_application.features.data_transformation.use_cases.data_transformation_usecase import DataTransformationUseCase
        data_transformation_usecase = DataTransformationUseCase(
            logger=logger, 
            transformer=transformer,
            storage=storage,          # <-- Injected
            odata_parser=odata_parser # <-- Injected
        )
        container["data_transformation_usecase"] = data_transformation_usecase
        print("  [+] Loaded Feature: DataTransformationUseCase")
    except ImportError as e:
        print(f"  [-] Skipped Feature: DataTransformationUseCase (ImportError: {e})")
    except Exception as e:
        print(f"  [⚠️ ] Failed to load Feature: DataTransformationUseCase (Error: {e})")
    
    # Feature: RowTransformationUseCase
    try:
        from app.layer2_application.features.data_transformation.use_cases.row_transformation_usecase import RowTransformationUseCase
        row_transformation_usecase = RowTransformationUseCase(
            logger=logger,
            storage=storage,
            row_transformer=row_transformer
        )
        container["row_transformation_usecase"] = row_transformation_usecase
        print("  [+] Loaded Feature: RowTransformationUseCase")
    except ImportError as e:
        print(f"  [-] Skipped Feature: RowTransformationUseCase (ImportError: {e})")
    except Exception as e:
        print(f"  [⚠️ ] Failed to load Feature: RowTransformationUseCase (Error: {e})")
    
    # Feature: SchemaTransformUseCase
    try:
        from app.layer2_application.features.schema_transform.use_cases.schema_transform_usecase import SchemaTransformUseCase
        schema_transform_usecase = SchemaTransformUseCase(
            logger=logger,
            transformer=schema_transformer
        )
        container["schema_transform_usecase"] = schema_transform_usecase
        print("  [+] Loaded Feature: SchemaTransformUseCase")
    except ImportError as e:
        print(f"  [-] Skipped Feature: SchemaTransformUseCase (ImportError: {e})")
    except Exception as e:
        print(f"  [⚠️ ] Failed to load Feature: SchemaTransformUseCase (Error: {e})")

    # Feature: RuleManagementUseCase
    try:
        from app.layer2_application.features.rule_management.use_cases.rule_management_usecase import RuleManagementUseCase
        rule_management_usecase = RuleManagementUseCase(
            logger=logger, 
            repository=rule_repository
        )
        container["rule_management_usecase"] = rule_management_usecase
        print("  [+] Loaded Feature: RuleManagementUseCase")
    except ImportError as e:
        print(f"  [-] Skipped Feature: RuleManagementUseCase (ImportError: {e})")
    except Exception as e:
        print(f"  [⚠️ ] Failed to load Feature: RuleManagementUseCase (Error: {e})")

    # Feature: TransformValidateBundleUseCase
    # Composes the two use cases above, so it must be registered after them.
    try:
        from app.layer2_application.features.bundle.use_cases.transform_validate_bundle_usecase import TransformValidateBundleUseCase
        if "schema_transform_usecase" in container:
            container["transform_validate_bundle_usecase"] = TransformValidateBundleUseCase(
                logger=logger,
                schema_transform_usecase=container["schema_transform_usecase"],
                data_validation_usecase=container.get("data_validation_usecase"),
            )
            print("  [+] Loaded Feature: TransformValidateBundleUseCase")
        else:
            print("  [-] Skipped Feature: TransformValidateBundleUseCase (SchemaTransformUseCase unavailable)")
    except ImportError as e:
        print(f"  [-] Skipped Feature: TransformValidateBundleUseCase (ImportError: {e})")
    except Exception as e:
        print(f"  [⚠️ ] Failed to load Feature: TransformValidateBundleUseCase (Error: {e})")

    # Feature: BuildReferenceKeysetUseCase
    try:
        from app.layer2_application.features.reference_data.use_cases.build_reference_keyset_usecase import BuildReferenceKeysetUseCase
        build_reference_keyset_usecase = BuildReferenceKeysetUseCase(
            logger=logger,
            storage=storage,
        )
        container["build_reference_keyset_usecase"] = build_reference_keyset_usecase
        print("  [+] Loaded Feature: BuildReferenceKeysetUseCase")
    except ImportError as e:
        print(f"  [-] Skipped Feature: BuildReferenceKeysetUseCase (ImportError: {e})")
    except Exception as e:
        print(f"  [⚠️ ] Failed to load Feature: BuildReferenceKeysetUseCase (Error: {e})")

    # Load default rules from JSON file (only if rule management is loaded)
    if "rule_management_usecase" in container:
        print("  [+] Loading default validation rules...")
        try:
            await load_default_rules(rule_repository)
        except Exception as e:
            print(f"  [⚠️ ] Failed to load default rules: {e}")
    
    print("✅ Dependency Injection Container built successfully!")
    
    return container


async def load_default_rules(rule_repository: RuleRepository):
    """Load default rules from validation_rules_db.json if they don't exist"""
    try:
        # Check if rules already exist
        existing = await rule_repository.find_all()
        if existing:
            print(f"    📋 Found {len(existing)} existing rule sets, skipping load")
            return

        # Load from JSON file
        json_path = os.path.join(os.path.dirname(__file__), "validation_rules_db.json")
        if not os.path.exists(json_path):
            print(f"    ⚠️  Rules file not found: {json_path}")
            return

        with open(json_path, 'r') as f:
            data = json.load(f)

        rules = data.get("rules", [])
        if not rules:
            print("    ⚠️  No rules found in JSON file")
            return

        # Create default rule set
        rule_set = RuleSet(
            name="Default Validation Rules",
            rules=rules,
            description="Default validation rules loaded from JSON file"
        )

        saved = await rule_repository.save(rule_set)
        print(f"    ✅ Loaded {len(rules)} default rules into RuleSet: {saved.id}")

    except Exception as e:
        print(f"    ❌ Error loading default rules: {e}")
