#!/usr/bin/env bash
# usage: ./run.sh [project_dir]
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then echo "먼저 ./install.sh 를 실행하세요."; exit 1; fi
exec .venv/bin/python hullparam.py app "$@"
