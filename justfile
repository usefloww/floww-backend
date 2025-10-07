generate-migrations:
    #!/bin/bash
    export PYTHONPATH=$(pwd)
    export DATABASE_URL=postgresql+asyncpg://admin:secret@localhost:5432/postgres
    alembic revision --autogenerate -m "migration"