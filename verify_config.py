#!/usr/bin/env python3
"""Simple script to verify config.example.yaml structure without dependencies."""

import yaml
from pathlib import Path


def verify_config_structure():
    """Verify config.example.yaml has the expected structure."""
    config_file = Path("config.example.yaml")

    if not config_file.exists():
        print("✗ config.example.yaml not found")
        return False

    try:
        with open(config_file, 'r') as f:
            config = yaml.safe_load(f)
    except Exception as e:
        print(f"✗ Failed to parse config.example.yaml: {e}")
        return False

    errors = []

    # Check required top-level keys
    required_keys = ['sources', 'search_criteria']
    for key in required_keys:
        if key not in config:
            errors.append(f"Missing required key: {key}")

    # Check sources structure
    if 'sources' in config:
        if not isinstance(config['sources'], list):
            errors.append("'sources' must be a list")
        elif len(config['sources']) == 0:
            errors.append("'sources' list is empty")
        else:
            for idx, source in enumerate(config['sources']):
                if not isinstance(source, dict):
                    errors.append(f"Source {idx} is not a dictionary")
                    continue

                required_source_keys = ['name', 'type', 'identifier']
                for key in required_source_keys:
                    if key not in source:
                        errors.append(f"Source {idx} missing key: {key}")

                if 'type' in source:
                    valid_types = ['greenhouse', 'lever', 'ashby']
                    if source['type'] not in valid_types:
                        errors.append(f"Source {idx} has invalid type: {source['type']}")

    # Check search_criteria structure
    if 'search_criteria' in config:
        sc = config['search_criteria']
        if not isinstance(sc, dict):
            errors.append("'search_criteria' must be a dictionary")
        else:
            # Check that at least one is present
            has_required = sc.get('required_terms') and len(sc.get('required_terms', [])) > 0
            has_groups = sc.get('keyword_groups') and len(sc.get('keyword_groups', [])) > 0

            if not has_required and not has_groups:
                errors.append("search_criteria must have at least one of: required_terms or keyword_groups")

    # Check optional keys have correct types
    optional_checks = {
        'scan_interval': str,
        'email': dict,
        'logging': dict,
        'advanced': dict,
    }

    for key, expected_type in optional_checks.items():
        if key in config and not isinstance(config[key], expected_type):
            errors.append(f"'{key}' must be of type {expected_type.__name__}")

    if errors:
        print("✗ config.example.yaml validation failed:")
        for error in errors:
            print(f"  - {error}")
        return False
    else:
        print("✓ config.example.yaml structure is valid")
        print(f"  - {len(config['sources'])} sources configured")
        print(f"  - Scan interval: {config.get('scan_interval', 'not set')}")

        sc = config.get('search_criteria', {})
        print(f"  - {len(sc.get('required_terms', []))} required terms")
        print(f"  - {len(sc.get('keyword_groups', []))} keyword groups")
        print(f"  - {len(sc.get('exclude_terms', []))} exclusion terms")
        return True


if __name__ == "__main__":
    import sys
    success = verify_config_structure()
    sys.exit(0 if success else 1)
