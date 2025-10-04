setup-packages:
    #!/bin/bash
    if [ -z "$GL_PAT" ]; then
        echo "!!! GL_PAT (gitlab personal access token) environment variable is not set !!!"
        exit 1
    fi

    # Check if .venv directory exists
    if [ ! -d ".venv" ]; then
        uv venv
    fi

    # Source the correct activate script depending on the shell
    if [ -n "$BASH_VERSION" ]; then
        source .venv/bin/activate
    elif [ -n "$ZSH_VERSION" ]; then
        source .venv/bin/activate.zsh
    elif [ -n "$FISH_VERSION" ]; then
        source .venv/bin/activate.fish
    else
        echo "Unsupported shell"
        exit 1
    fi

    uv pip install -r requirements/requirements_local.txt

setup-env:
    op inject -i .env.dev -o .env

setup-hooks:
    git config core.hooksPath .githooks/

setup:
    #!/bin/bash
    just setup-env
    just setup-hooks
    just setup-packages

generate-ci:
    #!/bin/bash
    source .venv/bin/activate
    python .gitlab-ci.py
