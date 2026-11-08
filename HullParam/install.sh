#!/usr/bin/env bash
# HullParam install (macOS / Linux): creates .venv and installs requirements
set -e
cd "$(dirname "$0")"
PY=""
for v in python3.13 python3.12 python3.11 python3.14 python3.10 python3; do
  if command -v $v >/dev/null 2>&1 && $v -c 'import sys; assert sys.version_info[:2] >= (3,10)' 2>/dev/null; then PY=$v; break; fi
done
if [ -z "$PY" ]; then echo "Python 3.10 - 3.14 가 필요합니다."; exit 1; fi
echo "사용 Python: $PY"
$PY -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -c "import OCP, shapely, yaml, numpy; print('설치 확인 OK')"
echo "설치 완료. ./run.sh 를 실행하세요."
