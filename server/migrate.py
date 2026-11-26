"""Create tables and seed demo data. Run once per release, before the API replicas start."""
import os

os.environ["AUTO_MIGRATE"] = "0"
os.environ["ROLE"] = "all"

import main  # noqa: E402
import solo  # noqa: E402,F401  (registers its tables)

main.migrate()
print("schema ready")
