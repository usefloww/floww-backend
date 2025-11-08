generate-migrations:
    #!/bin/bash
    docker compose up -d db
    source .venv/bin/activate
    export DATABASE_HOST=localhost
    export PYTHONPATH=$(pwd)
    export DATABASE_URL=postgresql+asyncpg://admin:secret@localhost:5432/postgres
    alembic upgrade head
    alembic revision --autogenerate -m "migration"

migrate:
    #!/bin/bash
    source .venv/bin/activate
    export DATABASE_HOST=localhost
    export PYTHONPATH=$(pwd)
    export DATABASE_URL=postgresql+asyncpg://admin:secret@localhost:5432/postgres
    alembic upgrade head

test-unit files:
    #!/bin/bash
    source .venv/bin/activate
    export DATABASE_HOST=localhost
    export DATABASE_URL=postgresql+asyncpg://admin:secret@localhost:5432/postgres

    for file in {{files}}; do
        echo "Running $file"
        pytest $file
    done
