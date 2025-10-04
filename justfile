generate-migrations:
    #!/bin/bash
    export PYTHONPATH=$(pwd)
    alembic revision --autogenerate -m "migration"