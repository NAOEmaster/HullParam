# HullParam — 선체 구조 파라메트릭 CAD 생성기 (Shell + Beam)

선체 **전체 CAD(외판 + 내부 부재)** 를 넣으면 부재를 자동으로 인식해 **파라미터(params.yaml)** 로 만듭니다.
그 파라미터를 바꾸면(두께, 늑골·격벽의 개수/간격, 보강재 개수/간격 …) **온전한 CAD**(쉘 + 빔)가 다시 생성되고,
**무결성 검사**를 거쳐 **STEP / IGES / BREP** 로 출력됩니다. ANSYS 스타일 화면에서 확인·편집할 수 있습니다.

- 형상 커널: OpenCASCADE (`cadquery-ocp`, 오픈소스) — COMSOL·ANSYS 라이선스 불필요
- 입력: STEP(권장) / IGES / BREP  (+ 선택: COMSOL `.mph` → 두께·보강재 단면·격벽/트랜섬 보강재 위치)
- 출력: 이름 붙은 STEP 어셈블리(쉘·빔), 쉘 STEP, 빔 STEP/IGES, 연결이 유지된 BREP, 물성표 CSV, 검사 리포트

---

## 1. 설치 (한 번만)

1. **Python 3.10 ~ 3.14 (64-bit)** 설치 — https://www.python.org/downloads/windows/ (설치 화면에서 *Add python.exe to PATH* 체크)
2. 이 폴더에서 **`install.bat`** 더블클릭 (macOS/Linux: `./install.sh`)
   - 가상환경은 경로 길이·OneDrive 동기화 문제를 피하려고 **`%LOCALAPPDATA%\HullParam\venv`** 에 만듭니다
     (프로그램 폴더는 한글/공백이 들어간 OneDrive 경로여도 됩니다).
   - 패키지 약 200 MB, 1~3분. 모든 과정이 **`install_log.txt`** 에 기록됩니다 — 실패하면 이 파일을 보내주세요.

## 2. 실행

- **`run.bat`** 더블클릭 → 브라우저에 화면이 열립니다 (검은 창은 서버이므로 사용 중에는 닫지 마세요) (주소 `http://127.0.0.1:8765`, 내 PC 안에서만 동작).
- 프로젝트 폴더를 `run.bat` 위로 끌어다 놓으면 그 프로젝트가 바로 열립니다.
- 예제: `examples/GPPH_A4-B1_project` (GPPH A4-B1 CAD + COMSOL에서 추출한 프로젝트) → **📂 프로젝트 열기**로 선택.

## 3. 사용 순서 (화면)

| 순서 | 버튼 | 내용 |
|---|---|---|
| ① | **⤓ CAD 가져오기** | 전체 CAD(STEP) 선택, (선택) COMSOL `.mph` 지정 → 자동 추출 (1~2분). `<CAD이름>_project` 폴더가 생깁니다. |
| ② | 오른쪽 **Parameters** | 탭: 배치(늑골·격벽 표) / 종부재(거더·수평판) / 보강재 / 두께·재료 / YAML(전체 직접 편집) |
| ③ | **✓ 검증** | 형상 생성 없이 입력 확인 + 늑골/격벽 위치를 3D에 미리보기(반투명 평면) |
| ④ | **▶ 생성** | 저장 → 형상 생성 → 무결성 검사 (GPPH 기준 약 1~3분, 진행률 표시) |
| ⑤ | 아래 **검사 결과** | 📍가 붙은 항목을 클릭하면 해당 위치로 이동·강조 |
| ⑥ | **⇪ 내보내기** | STEP/IGES/BREP/CSV/JSON → `프로젝트/output/<시각>/` |

화면 조작: 왼쪽 드래그 회전, 오른쪽/가운데 드래그 이동, 휠 확대, 클릭 선택(왼쪽 Details에 두께·단면·면적 표시),
트리 더블클릭 확대, `F` 전체 맞춤, `Esc` 선택 해제, `H` 선택 숨기기. 상단 도구: 시점, 모서리/빔 표시, 외판 투명도,
**색상: 두께/단면적**, **단면(x) 절단**, 격리 보기.

---

## 4. 파라미터 설명 (`params.yaml`)

단위 mm, 좌표 x 선수(+), y 좌현(+), z 위(+). 대칭 부재는 |y| 로 한쪽만 입력합니다.

### x 참조
거더·수평판·stringer 의 시작/끝에는 숫자 대신 **`aft`, `fwd`, `BH1..BHn`(격벽), `F1..Fn`(늑골)**, `F3+20` 같은
오프셋도 쓸 수 있습니다. 참조를 쓰면 격벽/늑골이 움직일 때 끝단도 따라가고, 그 부재의 실제 면 위에서 정확히 잘립니다.
> 늑골 개수를 바꾸면 `F5` 가 가리키는 늑골도 바뀝니다. 개수를 자주 바꾸는 경우 끝단은 `BHn` 참조를 권장합니다.

### 위치에 따라 변하는 값 (표)
숫자 하나 또는 `{x: [..], value: [..]}` (선형보간, 범위 밖은 끝값). 화면에서는 `1327.6:207.3, 2655.2:210.7` 형식.
→ 늑골 개수를 바꿔도 각 늑골 위치에서 보간된 두께/웨브 깊이가 자동 적용됩니다.

### 늑골·격벽 배치표 `stations` (v1.1, 화면 **배치** 탭)
늑골과 격벽을 **한 표**로 관리합니다. 행마다 `x`, `type`(`frame`/`bulkhead`), `thickness`, (늑골) `web_depth`,
`deck_beam_depth`, `corner_radius`, (격벽) `stiffeners` 를 가집니다. 비워 둔 칸은 아래 공통값(`frames`/`bulkheads`)을 따릅니다.

| 화면 | 동작 |
|---|---|
| ① 자동 배치 | **맨 앞 / 맨 뒤 위치 + 간격** 입력 → 개수 = round((뒤−앞)/간격), 실제 간격은 균등하게 자동 조정. "격벽 유지"를 켜면 기존 격벽 위치는 그대로 두고 가장 가까운 늑골을 대체 |
| ② 단면 미리보기 | 표에서 고른 행의 실제 단면(외판 단면 + 개구부)을 2D로 표시, 면적 표시 |
| ③ 표 | 이름(F1.., BH1..) · 종류 선택(**늑골 ↔ 격벽 전환**) · x · **앞 간격**(고치면 뒤쪽 행이 함께 이동) · 두께 · 웨브 깊이 · 갑판보 깊이 · 모서리 R · 행 추가(＋)/삭제(✕) |
| ④ 일괄 수정 | 모든 늑골의 두께/웨브/갑판보/R 를 한 번에 |
| ⑤ 플랜지 | 개구부 테두리 빔 단면 |

`stations.list` 가 비어 있으면 예전 방식(`bulkheads`·`frames` 의 mode)이 쓰입니다.

### 격벽 `bulkheads` / 늑골 `frames` (공통값 + 예전 방식)
- `bulkheads.thickness`, `stiffeners`: 공통 보강재 패턴 (`vertical`: |y| 또는 시작·간격·개수, `z_from`/`z_to`(`hull`/`deck`), 단면 / `horizontal`: z, `y_max`).
  우선순위: 표의 행 `stiffeners` > `per_bulkhead[i]` > 공통
- `frames`: 공통 `thickness`, `web_depth`, `deck_beam_depth`, `corner_radius`, `flange`
- 예전 방식: `frames.mode: per_compartment | spacing | list`, `bulkheads.mode: list | equal`
- 형상: 단면을 외판에서 `web_depth` 만큼 안쪽으로 offset 한 개구부를 뚫고, 갑판 아래 `deck_beam_depth` 는 막음,
  모서리 반경 `corner_radius` (개구부가 좁으면 자동으로 줄이고 경고)

### 거더 `girders`
`y`(|y|), `symmetric`, `thickness`, `segments: [{x_from, x_to, z_top}]` — `z_top` 은 숫자 또는 표(경사 상단, 예: 엔진 램프),
`flange`: 상단 빔.

### 수평판 `plates`
`z`, `x_from/x_to`(참조 가능), `y_from/y_to`, `thickness` (예: 엔진 받침판)

### 종보강재 `stringers` (빔, 화면 **보강재** 탭)
- `bottom`, `deck`: |y| 위치 / `side`: z 위치
- **`mode: auto`** (v1.1): **중심선(y=0) 포함 여부** 체크 + **간격** + **한쪽(half) 개수** → 위치 = 간격×1, ×2, … 자동
  (`skip_near_girders` 이내의 거더 근처는 생략). 중심선 체크는 `centerline.keel`(바닥) / `centerline.deck`(갑판) 빔
- `mode: list` (`positions`) / `mode: spacing` (`start`, `spacing`, `count`)
- `x_from/x_to` 또는 개별 `extents` (원본 CAD 길이 — 개수를 바꾸면 자동 무시)
- `section`: 하나 또는 보강재별 목록. 종류 **T** (`height, width, t_web, t_flange`), **L(앵글)** (같은 항목),
  **FLAT(평강)** (`height, thickness`). 화면 표에서 보강재마다 종류/치수를 고르고,
  **"그룹 전체 같게"**, **"모든 보강재 같게"**(체크한 그룹에 한 단면 적용) 버튼 제공

### 기타
`centerline`(용골·갑판 중심선 빔), `transom`(트랜섬 보강재, 격벽과 같은 형식), `plating`(외판 두께: deck/side/bottom/chine/transom),
`material`, `build`(공차·검사 기준).

---

## 5. 무결성 검사 (생성할 때마다 자동)

| 코드 | 내용 | 수준 |
|---|---|---|
| C01 | 형상 유효성 (BRepCheck). 두 모서리가 거의 접하게 만나 생기는 매개변수 공간 겹침은 3D 형상·면 분할을 직접 확인한 뒤 WARN | ERROR / WARN |
| C02 | 쉘 두께 / 빔 단면 누락 | ERROR |
| C03 | 판에 붙지 않은(떠 있는) 빔 | ERROR |
| C04 | 구조에서 분리된 부분 | ERROR |
| C05 | 틈(gap): 판 끝이 이웃 판에 닿을 듯 말 듯 연결 안 됨 | ERROR |
| C06 | 같은 면이 두 부재에 속함(겹침) | WARN |
| C07 | 짧은 모서리 / 작은 면 (메시 품질) | WARN |
| B00 | 생성 중 경고 (생략된 부재, 자동 보정 등) | WARN |
| C08 | 통계 (T-접합, 자유모서리) | INFO |

생성기는 sliver(얇은 띠 면)를 막기 위해 다음을 자동으로 합니다.
- 부재가 기존 외판 분할선과 `snap_tolerance`(2 mm) 이내면 그 분할선에 정확히 맞춥니다. GPPH 늑골처럼 미세하게 기운 COMSOL 평면도 그대로 재현합니다.
- 교차 후 B-spline 외판의 모서리 곡선 오차는 그 자리에서 수리하고, 커진 공차를 보고합니다.

## 6. 출력 파일 (`output/<시각>/`)

| 파일 | 용도 |
|---|---|
| `<이름>_assembly.step` | **ANSYS 권장**. SHELLS / BEAMS 하위에 부재별 이름·색상 |
| `<이름>_shells.step` | 모든 쉘 면 (한 바디) |
| `<이름>_beams.step`, `_beams.igs` | 빔 선 (DesignModeler/SpaceClaim 라인 바디) |
| `<이름>.brep` | 부재 간 연결(공유 모서리)이 그대로 유지된 정확한 모델 (OpenCASCADE/gmsh) |
| `<이름>_properties.csv` | 부재별 종류, 두께, 단면(치수·A·Iyy·Izz), 면적/길이 |
| `<이름>_model.json` | 부재 → BREP 면/모서리 번호 매핑 + 물성 (메시/Nastran 변환용) |
| `<이름>_check.json`, `_params.yaml` | 검사 결과, 사용한 파라미터 |

**ANSYS 에서:** SpaceClaim(또는 DesignModeler)에서 `_assembly.step` 열기 → 빔 단면은 CSV 값으로 지정 →
**Share Topology** (STEP은 부재 사이 공유 모서리를 담지 못하므로 필수). 쉘 두께는 CSV의 `thickness_mm`.

## 7. 일괄 생성 (데이터셋)

```
%LOCALAPPDATA%\HullParam\venv\Scripts\python.exe hullparam.py batch <프로젝트> examples\batch_example.yaml
```
케이스마다 `set:` 에 바꿀 값만 적습니다 (`stations.auto.spacing: 1000`, `stations.list.4.type: bulkhead`, `girders.0.thickness: 9` …).
`stations.auto.*` 를 바꾸면 배치표가 앞/뒤/간격으로 다시 만들어집니다(격벽 유지).
케이스별 폴더와 `batch_summary.csv`(상태·면적·길이·시간)가 만들어집니다.

명령줄: `hullparam.py extract <cad> -m <mph> -o <프로젝트>`, `hullparam.py build <프로젝트>` (생성+검사+출력).

## 8. 알아둘 점 (한계)

- **외판(hull skin)은 고정**입니다. 입력 CAD의 외판을 그대로 쓰며, 원본 외판의 분할선(옛 늑골·stringer 위치)은 면 경계로 남습니다.
  부재가 그 근처(2 mm 이내)면 자동으로 맞추고, 그보다 멀면 정상적인 면 경계가 됩니다. 분할되지 않은 깨끗한 외판 STEP(예: `NewAft_Hull.stp`)이
  있으면 더 깔끔한 결과를 얻을 수 있습니다.
- `.x_t`, `.sat` 는 오픈소스 커널로 읽을 수 없습니다 → 같은 모델의 **STEP** 을 사용하세요.
- 늑골은 "외판 offset + 갑판보 + 라운드" 모델입니다. GPPH 원본과의 일치도(IoU)는 0.95~0.97 (추출 리포트 탭에서 확인).
- COMSOL 트랜섬 보강재 좌표는 작업평면 좌표를 근사 변환한 값입니다 (트랜섬 밖으로 나가는 선은 경고와 함께 생략).
- 쉘 IGES 는 B-spline 면 정밀도가 떨어질 수 있어 기본 출력에서 제외했습니다 (STEP 사용).
