"""Validate staging settings with the same models used at process startup.

Never echo ValidationError text: user-provided URLs and secrets can appear in it.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Mapping

from pydantic import ValidationError

from mura.config import CoreSettings, StandaloneWorkerSettings


def check_staging_configuration(
    env_dict: Mapping[str, str] | None = None, *, role: str = "api"
) -> bool:
    if role not in {"api", "worker"}:
        raise ValueError("role must be api or worker")
    values = dict(os.environ if env_dict is None else env_dict)
    if values.get("MURA_ENVIRONMENT") != "staging":
        print("FAIL: MURA_ENVIRONMENT must explicitly be staging")
        return False
    settings_class = CoreSettings if role == "api" else StandaloneWorkerSettings
    try:
        settings_class.model_validate(values)
    except ValidationError as exc:
        for name in sorted(
            {
                str(error["loc"][0]) if error["loc"] else "runtime invariants"
                for error in exc.errors()
            }
        ):
            print(f"FAIL: {name} missing or invalid")
        return False
    print(f"PASS: staging {role} runtime settings validated (secret values hidden)")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=("api", "worker"), required=True)
    args = parser.parse_args()
    raise SystemExit(0 if check_staging_configuration(role=args.role) else 1)
