# ChessBot 2.0 NNUE

**UCI-шахматный движок на C++20** с битбордами, magic bitboards, альфа-бета поиском и
собственной инфраструктурой обучения небольшой нейросети.

Это учебно-инженерный проект, а не замена Stockfish. Движок умеет играть полноценные
партии по UCI, проходить perft, запускать bench и обучать/подключать экспериментальную
оценку 768 → 64 → 1.

> **Коротко о статусе.** Классическая оценка — основной надёжный режим. Нейросеть
> реализована и обучается, но по результатам быстрых матчей не показала прироста силы;
> включать её для игры «на результат» не рекомендуется. Полный Stockfish 19 с его
> NNUE-сетью значительно сильнее. Утверждение «не хуже Stockfish» для этого проекта
> честно сделать нельзя.

## Содержание

- [Что это](#что-это)
- [Возможности](#возможности)
- [Быстрый старт](#быстрый-старт)
- [Сборка](#сборка)
- [Запуск и UCI](#запуск-и-uci)
- [Нейросеть: включение и обучение](#нейросеть-включение-и-обучение)
- [Сила игры: честные результаты](#сила-игры-честные-результаты)
- [Тесты и измерения](#тесты-и-измерения)
- [Структура проекта](#структура-проекта)
- [Ограничения и roadmap](#ограничения-и-roadmap)
- [Лицензия и Stockfish](#лицензия-и-stockfish)
- [Документация](#документация)

## Что это

ChessBot 2.0 NNUE — самодостаточный движок без внешних библиотек для шахматной логики.
Он читает UCI-команды из stdin, пишет ответы в stdout и может быть подключён к любой
UCI-совместимой оболочке (Arena, CuteChess, BanksiaGUI, Nibbler и т. п.).

Основные принципы проекта:

- понятная реализация всех ключевых частей движка «с нуля»;
- детерминированная генерация ходов и проверяемый perft;
- прозрачная, настраиваемая оценка без закрытых оценочных таблиц;
- отдельный тренер и генератор данных для экспериментов с нейросетевой оценкой;
- честная документация: измеренные результаты, ограничения и известные проблемы.

Движок **однопоточный** в части поиска. Отдельный рабочий поток используется только для
того, чтобы UCI-интерфейс мог принимать `stop` во время поиска.

## Возможности

### Доска и правила

- Представление позиции: 6 битбордов по типам фигур + 2 по цветам + общий occupancy,
  дополнительный массив `board[64]` и `kingSq[2]`.
- Magic bitboards для слонов и ладей; таблицы атак генерируются при старте процесса.
  При сборке с BMI2 (`-march=native` на современных x86) используется `PEXT`,
  иначе — классический multiply-magic.
- Zobrist-хеширование с инкрементальным обновлением, включая права рокировки,
  сторону хода и en passant (только если взятие на проходе действительно возможно).
- Генерация псевдолегальных ходов, проверка легальности через make/unmake; на корне
  остаются только легальные ходы.
- FEN-парсинг и вывод, ASCII-доска (`d`).
- Определение ничьей: 50 ходов, повторение позиции, недостаточный материал.
- SEE (static exchange evaluation) для отсечения плохих разменов.
- `perft` для проверки генератора ходов.

### Поиск

- Итеративное углубление.
- Aspiration windows (с 4-й глубины, окно ±30, расширение при провале).
- Principal Variation Search (PVS).
- Транспозиционная таблица: 16 байт на запись, 32-битная сигнатура ключа,
  aging, границы, замена с учётом depth/поколения, mate-score correction.
- Null-move pruning.
- Late Move Reductions (LMR).
- Late Move Pruning (LMP).
- Futility pruning и reverse futility pruning.
- Razoring.
- SEE-отсечение шумных ходов.
- ProbCut: reduced-depth проверка хороших взятий с повышенным beta.
- Lazy SMP: `Threads` 1…16, общий lockless TT и stop, разделяемая история.
- Killers, history heuristic, countermove и capture history.
- Check extension.
- Internal Iterative Reduction (IIR).
- Quiescence search с delta pruning и SEE-отсечением.
- Mate-distance pruning.
- Syzygy: WDL-probe в поиске и DTZ-ход на корне через Fathom; включается
  `SyzygyPath`, без файлов ничего не меняется.
- Управление временем: `movetime`, `wtime`/`btime`, инкременты, `movestogo`,
  `Move Overhead`.
- UCI-протокол, `perft`, `bench`, `eval`, `d`, `Clear Hash`.

### Оценка

Классическая оценка — tapered (mg/eg), фаза считается по непешным фигурам:

- материал и piece-square tables (PeSTO-подобные масштабы);
- мобильность фигур;
- пара слонов;
- пешечная структура: сдвоенные, изолированные, проходные пешки, защищённый проходник;
- ладьи: открытая/полуоткрытая линия, 7-я горизонталь;
- коневые форпосты;
- безопасность короля: пешечный щит, открытая линия рядом с королём, давление на
  королевское кольцо;
- пространство в центре;
- mop-up в упрощённых эндшпилях;
- темп (+10 cp).

### Нейросетевая оценка (экспериментально)

- Небольшая сеть 768 → 64 → 1, ReLU на скрытом слое, float-инференс.
- 768 признаков — это 12 относительных типов фигур × 64 поля. Это **не** полноценный
  HalfKP с королевскими бакетами: король в признаках не участвует, аккумулятора нет,
  признаки пересчитываются на каждой оценке.
- Два варианта весов:
  - residual-сеть, обученная на self-play метках глубины 6;
  - direct-сеть, дистиллированная из статической оценки Stockfish 19.
- Режимы смешивания: аддитивная поправка к классической оценке или прямая замена
  с весом.
- Тренер и генератор данных: `trainer datagen`, `trainer train`, `trainer stats`.

Подробности — в [docs/ALGORITHMS.md](docs/ALGORITHMS.md) и
[docs/BENCHMARKS.md](docs/BENCHMARKS.md).

## Быстрый старт

Минимальный сценарий для Windows:

```powershell
# 1. Собрать (нужен g++ из MinGW-w64 в PATH)
powershell -ExecutionPolicy Bypass -File .\build.ps1

# 2. Запустить UCI-движок
.\chessbot.exe
```

В открывшемся stdin введите:

```text
uci
isready
position startpos
go depth 10
```

Дождитесь строки `bestmove ...`, затем введите `quit`.

Готовая короткая инструкция: [QUICKSTART.md](QUICKSTART.md).

## Сборка

### Требования

- Windows x64 (проверено на Windows 10 LTSC 21H2; проект также должен собираться
  MinGW/Clang на Linux/macOS, но это не проверялось в данном репозитории).
- Компилятор C++20 с поддержкой `<bit>` (`std::popcount`, `std::countr_zero`).
- MinGW-w64 (UCRT, POSIX-потоки) или другой GCC/Clang. Проверенная версия:
  `g++ 16.1.0` (MinGW-W64 x86_64-ucrt-posix-seh).
- PowerShell 5+ для `build.ps1`.
- CMake ≥ 3.16 — опционально.
- Python 3 и `python-chess` — только для арены и скриптов разметки.

MSVC в текущем виде **не тестировался**: в `CMakeLists.txt` есть ветка `if(MSVC)`,
но сборка и поведение под MSVC не проверялись. Исходники больше не используют
GCC-встроенные `__builtin_*` (вместо них `std::popcount`/`std::countr_zero`), поэтому
порт теоретически возможен, но требует отдельной проверки.

### Вариант 1: build.ps1

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

Скрипт собирает два исполняемых файла в корне проекта:

- `chessbot.exe` — UCI-движок;
- `trainer.exe` — генератор данных, обучение и статистика.

Флаги (см. `build.ps1`): `-std=c++20 -O3 -march=native -flto -DNDEBUG -Wall -Wextra
-pthread`, линковка `-flto -static-libgcc -static-libstdc++ -Wl,--stack,33554432`.

`-march=native` означает, что бинарник оптимизирован под CPU машины сборки. Для
переноса на другой компьютер пересоберите проект или замените флаг на
`-march=x86-64-v2` (см. раздел «Troubleshooting сборки» ниже).

### Вариант 2: CMake

```powershell
cmake -S . -B build -G "MinGW Makefiles"
cmake --build build -j
```

Результат появится в каталоге `build` (`build\chessbot.exe`, `build\trainer.exe`).
CMake-конфигурация добавляет те же `-O3 -march=native -flto` и 32-МБ стек.

### Вариант 3: Makefile

```powershell
mingw32-make -j
# или, если в PATH есть GNU make:
make -j
```

Результат — `chessbot.exe` и `trainer.exe` в корне.

### Вариант 4: PGO-сборка (release)

```powershell
.\build_pgo.ps1
```

Скрипт делает instrumented-сборку, прогоняет `bench` для сбора профиля и
пересобирает `chessbot_pgo.exe` с `-fprofile-use`, затем копирует его в
`chessbot.exe`. На этой машине это дало ~+5–6% NPS (1.33M → 1.40M на
`bench 12`, 1 поток). Профиль собирается во временном ASCII-каталоге:
MinGW-профайлер не умеет абсолютные пути с буквой диска. Для Lichess используйте
именно PGO-бинарник, если он прошёл QA.

### Troubleshooting сборки

| Симптом | Причина и решение |
|---|---|
| `g++ : не найден` / `g++ is not recognized` | MinGW не в `PATH`. Установите MSYS2/MinGW-w64 (UCRT64), добавьте `...\ucrt64\bin` в `PATH`, проверьте `where.exe g++`. |
| Ошибки `-flto` / `lto plugin` | Несовпадение плагина LTO и компилятора. Уберите `-flto` из флагов сборки или переустановите согласованный MinGW. |
| `undefined reference to ... pthread` | Нужен MinGW с POSIX-потоками и флаг `-pthread`. Используйте `x86_64-w64-mingw32-posix` toolchain. |
| `Illegal instruction` на другом ПК | Бинарник собран с `-march=native`. Пересоберите на целевом CPU или используйте `-march=x86-64-v2`. |
| Падение/переполнение стека на глубоком поиске | Убедитесь, что применяется `-Wl,--stack,33554432` (MinGW) или `/STACK:33554432` (MSVC, если порт появится). |
| `CMake` не находит компилятор | Укажите генератор `MinGW Makefiles` и добавьте MinGW в `PATH`; при необходимости задайте `-DCMAKE_CXX_COMPILER=g++`. |
| Сборка идёт очень долго | `-flto` + файл весов `src/nnue_weights.cpp` (~680 КБ) заметно замедляют линковку. Это нормально; для итераций можно временно убрать `-flto`. |
| Кириллица/пробелы в пути ломают сборку | По возможности используйте путь без не-ASCII символов; некоторые версии MinGW/CMake плохо обрабатывают такие каталоги. |
| Антивирус/SmartScreen блокирует `.exe` | Бинарник не подписан. Добавьте каталог в исключения или соберите локально. |

## Запуск и UCI

```powershell
.\chessbot.exe
```

Движок работает как обычный UCI-сервер. В GUI добавьте `chessbot.exe` как UCI engine.
Для воспроизводимых тестов удобно использовать `tools/arena.py`.

> После запуска движок уже в startpos: конструктор `UciEngine` вызывает
> `setStartpos()`. `ucinewgame` также сбрасывает позицию в startpos.

### UCI-опции

| Опция | Тип | По умолчанию | Диапазон | Назначение |
|---|---|---:|---|---|
| `Hash` | spin | 64 | 1…4096 | Размер транспозиционной таблицы в МБ. Изменение пересоздаёт TT. |
| `Threads` | spin | 1 | 1…16 | Потоки Lazy SMP: 1 + helpers. Больше потоков — выше NPS и сила на многоядерном CPU; 8 потоков проверены стресс-тестом. |
| `Move Overhead` | spin | 30 | 0…5000 | Запас времени в мс, вычитаемый из бюджета на ход. |
| `SyzygyPath` | string | `<empty>` | путь | Каталог с файлами `.rtbw`/`.rtbz`; несколько путей — через `;`. Пусто — Syzygy выключены. |
| `SyzygyProbeDepth` | spin | 1 | 1…100 | Минимальная глубина для WDL-probe в поиске. |
| `Syzygy50MoveRule` | check | `true` | `true`/`false` | Учитывать правило 50 ходов при probe. |
| `SyzygyProbeLimit` | spin | 7 | 0…7 | Максимум фигур для probe; дополнительно ограничивается наличием файлов. |
| `UseNNUE` | check | `false` | `true`/`false` | Включает нейросетевую поправку, если в сборке есть обученные веса. |
| `NNUEWeight` | spin | 25 | 0…200 | Вес сети при смешивании. 0 — сеть фактически отключена. |
| `NNUEClip` | spin | 80 | 0…1500 | Ограничивает модуль поправки сети (в сантипешках) до масштабирования весом. |
| `Clear Hash` | button | — | — | Очистить TT и историю поиска. |

Syzygy использует MIT-библиотеку Fathom (`third_party/fathom/`). Файлы таблиц
не входят в репозиторий:

```text
setoption name SyzygyPath value C:/syzygy/3-4-5
setoption name SyzygyProbeDepth value 1
setoption name Syzygy50MoveRule value true
setoption name SyzygyProbeLimit value 7
```

На корне движок берёт готовый ход из DTZ и печатает `info depth 0 ...`;
в поиске используется WDL-probe. Если файлов нет или позиция не покрыта,
поведение не меняется. Подробности — [docs/SYZYGY.md](docs/SYZYGY.md).

Фактическое значение `UseNNUE` по умолчанию берётся из скомпилированного файла весов.
В текущем `src/nnue_weights.cpp` стоит `enabled = false`: сеть **выключена по
умолчанию**, потому что в 144 парных матчах лучшая конфигурация дала около
−46 Elo (95% интервал ±55) при потере ~22% NPS. Для экспериментального включения:

```text
setoption name UseNNUE value true
setoption name NNUEWeight value 25
setoption name NNUEClip value 80
```

Семантика `NNUEWeight`:

- residual-сеть (`direct = false`): `total = classical + net * weight / 100`;
- direct-сеть (`direct = true`): `total = (net * weight + classical * (100 - weight)) / 100`.

При `weight = 100` residual-режим даёт `classical + net`, direct-режим — чистую сеть.
Значения больше 100 усиливают сеть и в direct-режиме экстраполируют, поэтому 0…100 —
разумный рабочий диапазон.

### UCI-команды

| Команда | Описание |
|---|---|
| `uci` | Идентификация и список опций. |
| `isready` | `readyok`; используется для синхронизации. |
| `ucinewgame` | Сброс TT/истории и установка startpos. |
| `position startpos [moves ...]` | Начальная позиция с ходами. |
| `position fen <FEN> [moves ...]` | Позиция из FEN с ходами. |
| `go wtime ... btime ... winc ... binc ... movestogo ...` | Поиск с контролем времени. |
| `go movetime <ms>` | Фиксированное время на ход. |
| `go depth <n>` | Поиск до глубины `n`. |
| `go nodes <n>` | Остановка по числу узлов. |
| `go mate <n>` | Поиск мата (глубина `2n`). |
| `go infinite` | Бесконечный поиск до `stop`. |
| `stop` | Остановить поиск. |
| `quit` | Завершить процесс. |
| `setoption name <Name> [value <Value>]` | Установить опцию. |
| `d` | Напечатать ASCII-доску и FEN (отладочная команда). |
| `eval` | Напечатать классическую оценку, выход сети, итог и флаги NNUE. |
| `perft <depth>` | Посчитать perft от текущей позиции (глубина ограничена 0…15). |
| `bench [depth]` | Прогон 6 фиксированных позиций (по умолчанию глубина 10). |
| `ponderhit` | Принимается и игнорируется; pondering не поддерживается. |

Если в `go` не задано ни времени, ни глубины/узлов и нет часов, поиск уходит в
`infinite`. `go ponder` не включает настоящий pondering: команда обрабатывается как
обычный поиск.

При ошибке разбора FEN `position fen ...` сбрасывает позицию в startpos и печатает
`info string invalid FEN, using startpos`.

### Пример UCI-сессии

```text
uci
setoption name Hash value 256
setoption name UseNNUE value false
isready
ucinewgame
position startpos moves e2e4 e7e5 g1f3
go movetime 1000
```

Дождитесь `bestmove ...`, затем:

```text
quit
```

> **Важно для скриптов.** Не отправляйте `quit` сразу после `go` через пайп: движок
> обработает `quit`, остановит поиск и может вернуть запасной ход. Читайте stdout до
> строки `bestmove`, и только потом завершайте процесс. Это же относится к `tests/probe*.py`:
> они ждут `bestmove` с таймаутом.

## Нейросеть: включение и обучение

### Что лежит в репозитории

| Файл | Что это |
|---|---|
| `src/nnue_weights.cpp` | Residual-сеть 768 → 64 → 1, обучена на self-play метках глубины 6. `direct = false`, `enabled = false`, `weight = 25`, `clip = 80`. Вкомпилирована, но по умолчанию не влияет на оценку. |
| `src/nnue_weights_placeholder.cpp` | Пустые веса (`trained = false`), чтобы собрать движок без сети. Не входит в текущие списки сборки. |
| `nnue_residual.cpp`, `nnue_trained.cpp` | Сгенерированные варианты residual-весов (артефакты экспериментов). |
| `nnue_sf.cpp` | Direct-сеть, дистиллированная из статической оценки Stockfish 19. `direct = true`, `enabled = false`, `weight = 25`, `clip = 80`. По умолчанию не компилируется. |
| `data_train.txt` | 28 759 self-play позиций (формат `FEN` + оценка описан ниже). |
| `sf_data.txt` | 65 443 позиции, размеченные статической оценкой Stockfish 19. |

Веса **вкомпилированы в бинарник**; движок не загружает `.nnue`-файлы во время работы.

### Включить/выключить

```text
setoption name UseNNUE value true
setoption name NNUEWeight value 25
setoption name NNUEClip value 80
```

или полностью классический режим:

```text
setoption name UseNNUE value false
```

Проверить состояние:

```text
eval
```

Пример вывода:

```text
info string classical(stm) 10 neural 0 total 10 nnue_available 1 enabled 0 weight 25 clip 80 direct 0
```

`nnue_available 0` означает, что сборка использует placeholder без обученных весов.
`direct 1` — direct-режим, `direct 0` — residual-режим.

### Собрать с direct-сетью

1. Замените `src/nnue_weights.cpp` на `nnue_sf.cpp` (или на свой сгенерированный файл).
2. Пересоберите проект.
3. При необходимости выставьте `NNUEWeight` вручную.

Не редактируйте веса вручную: файл генерируется тренером.

### Обучение

Тренер имеет три режима:

```text
trainer datagen <games> <depth> <out.txt> [seed]
trainer train <data.txt> <epochs> <out_nnue.cpp> [direct]
trainer stats <data.txt> [direct]
```

Пример residual-пайплайна:

```powershell
# 300 партий self-play, метки — оценка поиска на глубине 6
.\trainer.exe datagen 300 6 data_train.txt 20261123

# Обучение residual-сети; вывод — C++-файл с весами
.\trainer.exe train data_train.txt 35 src\nnue_weights.cpp

# Диагностика датасета
.\trainer.exe stats data_train.txt
```

Пример direct-пайплайна:

```powershell
# Разметка статической оценкой Stockfish 19 (нужен python-chess)
python tools\label_stockfish.py `
  --input data_train.txt `
  --output sf_data.txt `
  --random 40000 `
  --stockfish tools\sf19\stockfish\stockfish-windows-x86-64-universal.exe

# Обучение прямой сети
.\trainer.exe train sf_data.txt 40 src\nnue_weights.cpp direct
```

Для экспериментов безопаснее писать в отдельный файл (например, `nnue_new.cpp`),
сравнивать метрики/игру и только потом заменять `src/nnue_weights.cpp`.

Формат данных: одна позиция на строку, `FEN|score`, где `score` — оценка с точки
зрения стороны, которая ходит. Позиции с матовыми оценками (`|score| ≥ MATE_IN_MAX`)
при загрузке отбрасываются.

Тренер:

- делит данные 90/10 на train/validation;
- использует Adam, batch 64, начальный learning rate 0.003 с множителем 0.97 за эпоху;
- останавливается досрочно после 8 эпох без улучшения validation MSE;
- пишет C++-файл с массивами `W1`, `B1`, `W2`, `B2` и флагами `trained`, `enabled`,
  `direct`, `weight`.

Residual-режим учит поправку `search_score − classical_eval` (clamp ±800 cp), direct —
абсолютную оценку (clamp ±1500 cp). Подробнее о метриках — в
[docs/BENCHMARKS.md](docs/BENCHMARKS.md).

### Честно о результате NNUE

- Residual-сеть: 27 567 использованных позиций из 28 759 сгенерированных, 35 эпох,
  validation correlation ≈ 0.885.
- Direct-сеть: 65 443 позиции, 40 эпох, validation correlation ≈ 0.970.
- Высокая корреляция не означает прироста силы: метки residual-сети — это оценки
  поиска глубины 6, а direct-метки — сжатая статическая оценка Stockfish.
- В быстрых матчах включение сети с большим весом приводило к поражениям от
  классической оценки. В коротких сериях `experiments/nnue/logs` ни одна
  конфигурация сети не превысила 50% (детали — в
  [docs/BENCHMARKS.md](docs/BENCHMARKS.md)). Direct-сеть недооценивает материал,
  потому что `eval` Stockfish сжат по шкале.
- Поэтому текущая рекомендация: **для игры используйте `UseNNUE=false`**, а сеть
  рассматривайте как исследовательский стенд.

## Сила игры: честные результаты

В ходе разработки были сыграны короткие матчи против Stockfish 19 с включённым
ограничением силы (`UCI_LimitStrength=true`, `UCI_Elo=...`), контроль — 100 мс на ход.
Финальная сборка, зеркальные пары дебютов, NNUE выключена:

| Соперник | Результат ChessBot | Партий | PGN |
|---|---:|---:|---|
| Stockfish 19, `UCI_Elo=2200` | +5 =1 −4 (0.550) | 10 | `results/match_final_sf2200.pgn` |
| Stockfish 19, `UCI_Elo=2500` | +4 =0 −6 (0.400) | 10 | `results/match_final_sf2500.pgn` |

Оценка по этим 10-партийным точкам — **примерно 2200–2350 Elo**, ориентир ~2250. Это движковый
Elo, не человеческий рейтинг; 10 партий на точку дают очень широкий доверительный
интервал. Ранее на меньших выборках были +5 −1 против 1800, +4 −1 −2 против 2200 и
+1 −5 против 2500; эти числа несопоставимы по составу и приведены как история.

После Lazy SMP, ProbCut и PGO-сборки был сыгран более длинный матч: 100 парных игр
на 50 дебютов, 80 мс/ход, соперник — Stockfish 19 с `UCI_Elo=2400`:

| Наша конфигурация | Счёт | Оценка Elo | 95% CI |
|---|---:|---:|---:|
| `Threads=1` | 46.0% | −28 | −95…+37 |
| `Threads=4` | 47.5% | −17 | −95…+59 |

Это даёт ориентир **около 2350–2450**, но интервал всё ещё слишком широк, чтобы
заявлять точное число. Четыре потока на 100 играх дали лишь небольшой перевес,
в пределах шума.

Разные соперники и малые выборки не позволяют выводить точный рейтинг Elo.
Сырые PGN матчей лежат в `results/`; старые серии были короче.

Что важно понимать:

- `UCI_Elo` у Stockfish — это режим ограничения силы, а не точный рейтинг движка.
- Полный Stockfish 19 (без `UCI_LimitStrength`) значительно сильнее: у него
  полноценная NNUE-сеть порядка 109 МиБ (SFNNv16-класс), многопоточный поиск, Syzygy,
  годы тюнинга и миллиарды тренировочных позиций; по опубликованным оценкам это
  уровень ~3600+ Elo. Наш движок ему не конкурент.
- Поражение 4–6 против `UCI_Elo=2500` на 100 мс/ход — ожидаемый результат для
  учебного движка с классической оценкой и одним потоком.
- Заявление «не хуже Stockfish» невозможно подтвердить и не делается.

## Тесты и измерения

### Perft

Проверенные значения (подробные замеры — в [docs/BENCHMARKS.md](docs/BENCHMARKS.md)):

| Позиция | Глубина | Узлы |
|---|---:|---:|
| Startpos | 4 | 197 281 |
| Startpos | 5 | 4 865 609 |
| Startpos | 6 | 119 060 324 |
| Kiwipete | 4 | 4 085 603 |
| Kiwipete | 5 | 193 690 690 |
| CPW Position 3 | 4 | 43 238 |
| CPW Position 4 | 4 | 422 333 |
| CPW Position 5 | 4 | 2 103 487 |
| CPW Position 6 | 4 | 3 894 594 |

Perft в этом движке считает только легальные ходы: `makeMove` проверяет, не остался ли
король под шахом, и откатывает нелегальный ход. Это удобно для отладки, но медленнее
«чистого» bulk-perft.

### Bench

```powershell
.\chessbot.exe
# в stdin:
position startpos
bench 10
```

На машине разработки (Ryzen 7 5800H, один поток, Hash 64 МБ, NNUE выключена,
финальная PGO-сборка) `bench 10` даёт около 961 тыс. узлов и ~1,43 млн узлов/с.
`bench 12` — 2,70 млн узлов и ~1,40 млн узлов/с. Включение NNUE стоит примерно
18–22% NPS. Числа зависят от CPU, компилятора и включённой сети; это не рейтинг
силы.

### Тактические задачи Lichess

Стенд `tools/puzzle_test.py` берёт 400 случайных задач из официальной базы Lichess
(CC0) и проверяет все ходы решения. Финальная PGO-сборка, NNUE выключена:

| Режим | Решено | Первый ход |
|---|---:|---:|
| 100 мс/ход, 1 поток | 355/400 = **88.8%** | 90.8% |
| 100 мс/ход, 4 потока | 363/400 = **90.8%** | 92.2% |
| 1000 мс/ход, 1 поток | 382/400 = **95.5%** | 96.0% |

На задачах с рейтингом 2600+ при 1 с решено 8/11 (72.7%); на 2200–2599 —
24/31 (77.4%). Повтор на независимой выборке 400 задач дал 87.5% при 100 мс
(в пределах интервала). Это шкала сложности Lichess-пазлов, **не Elo движка**.
Для контекста: на тех же 400 задачах при 100 мс Stockfish 19 с
`UCI_Elo=2400` решает тоже 355/400, но 28 задач у него уникальные, а 28 —
у ChessBot. Методика и оговорки: [docs/PUZZLES.md](docs/PUZZLES.md).

### Арена

```powershell
pip install chess
python tools\arena.py --games 20 --movetime 100 `
  --engine-a .\chessbot.exe `
  --engine-b .\tools\sf19\stockfish\stockfish-windows-x86-64-universal.exe `
  --option-b UCI_LimitStrength=true --option-b UCI_Elo=1800
```

`tools/arena.py` играет зеркальные пары дебютов (каждый дебют обоими цветами),
сохраняет полную историю ходов, поддерживает PGN (`--pgn`) и часы
(`--wtime/--inc/--movestogo`). При таймауте/сбое движок перезапускается, поэтому
старый `bestmove` не может протечь в следующую партию. Печатает W/D/L, Elo и
доверительный интервал; на малых выборках оценка имеет огромную погрешность, не
публикуйте её как рейтинг.

### Автотесты

В `tests/` есть скриптовый набор (не CI): `run_perft.py`, `run_protocol.py`,
`run_timing.py`, `run_robustness.py`, `run_selfplay.py`, общие `testutil.py` и
`uci_engine.py`. Логи складываются в `tests/logs/`, self-play PGN — в `tests/pgn/`.

QA-прогон после исправлений (2026-11-24, текущий `src/`):

| Набор | Результат | Что проверяет |
|---|---:|---|
| `perft` | 54/54 | perft startpos/Kiwipete/CPW 3–5 и task-FEN, краевые случаи: рокировка, en passant, превращения, шах/мат |
| `protocol` | 105/105 | UCI-опции, `position`, `go`, `stop`, `ucinewgame`, `d`, `eval`, `bench`, `perft` |
| `timing` | 59/59 | `movetime`, часы, `Move Overhead`, `go infinite` + `stop`, задержка остановки |
| `robustness` | 104/104 | нештатный ввод, пустые/невалидные FEN, крайние значения опций, живучесть процесса |
| `selfplay` | 36/36 | 10 партий, 1 506 полуходов, легальность каждого хода, PGN round-trip |

Итого: **358/358 проверок**, 0 провалов. Подробный отчёт — `tests/REPORT.md`;
свежий лог — `tests/logs/run_final.log`; сырые отчёты — `tests/logs/*.json`/`*.log`,
PGN — `tests/pgn/`.

Исторический прогон до фиксов дал 351/358 и выявил падения на пустой доске,
битом FEN и `std::stoi`; эти проблемы исправлены. Дополнительно в `src/`:
`UciEngine` ставит startpos в конструкторе, `go` при отсутствии королей
сбрасывает позицию, невалидный FEN сбрасывает в startpos, `Hash`/`Move Overhead`/
`NNUEWeight` и часы FEN парсятся безопасно, `d` делает flush, `inCheck`/
`isSquareAttacked`/`makeMove`/null-move защищены, добавлены countermove,
capture history, depth-aware TT и исправлен порядок king safety.

Perft-счётчики не меняются; bench/search-числа зависят от текущей оптимизации.

`selfplay` — это стресс-тест стабильности, а не измерение силы: движок играет сам с
собой. Результат 2 победы белых, 4 победы чёрных, 4 ничьи и среднее время 32,9 мс/ход
при `movetime 40` не является рейтингом.

Отдельный стресс для 8 потоков:

```powershell
python tests\run_stress.py --cycles 120 --threads 8 --movetime 40
```

Он гоняет `go movetime`/`go nodes`/`go infinite`+`stop`, `isready` во время поиска,
смену `Threads`/`Hash`, root и node Syzygy probe. Свежий прогон: 120 циклов,
**0 ошибок, 0 таймаутов, 0 нелегальных ходов** (`tests/logs/stress.json`).

## Обучение HalfKP-1024

В `training/` лежит автономный пайплайн: скачивание Lichess evals, извлечение
HalfKP-фич, обучение value-сети и экспорт весов. Подробности —
`training/README.md`.

```bash
python -m pip install -r training/requirements.txt
python training/auto_train.py --dataset lichess --positions 20000000 --device cuda
```

Smoke-проверка без скачивания:

```bash
python training/auto_train.py --dataset quiet --smoke
```

Модель не включается в движок автоматически: сначала нужны C++ HalfKP-
аккумулятор, замер NPS и матчи. 90 ГБ VRAM достаточно для FT 1024, но
качество зависит от данных и не гарантирует 3000 Elo.

## Публикация на Lichess

Подробная инструкция: **[docs/LICHESS.md](docs/LICHESS.md)**. Кратко:

1. Создайте **новый** аккаунт Lichess без сыгранных партий.
2. Выпустите OAuth-токен со scope **только `bot:play`**. `board:play`,
   `challenge:*`, `engine:*`, `puzzle:*`, `study:*`, `tournament:*`, `team:*`
   для игры бота не нужны.
3. Апгрейдните аккаунт:
   `curl -d "" https://lichess.org/api/bot/account/upgrade -H "Authorization: Bearer <TOKEN>"`.
4. Установите `lichess-bot`, скопируйте `lichess/config.yml.example` в
   `lichess-bot/config.yml`, укажите путь к `chessbot.exe` и токен.
5. Запустите `python lichess-bot.py`, вступите в команду
   [Leaderboard of BOTs](https://lichess.org/team/leaderboard-of-bots).

Для лидерборда нужно ≥ 50 рейтинговых партий в контроле, RD < 75 в standard,
рейтинг без `?` и хотя бы одна рейтинговая партия в неделю. Не «фармите» слабых
ботов и не играйте преимущественно со своими аккаунтами.

По умолчанию в примере `UseNNUE: false`, `Threads: 2`, без книги и ponder.
`Threads` работает через Lazy SMP; на 4 потоках локально ~6.5M NPS. Ожидаемый
уровень на Lichess — примерно 2300–2450, не 3000; это нужно подтвердить
рейтинговыми партиями, а не заявлять заранее.

### Соответствие требованиям к UCI-боту

| Требование | Статус |
|---|---|
| UCI | да: `uci/isready/ucinewgame/position/go/stop/quit/setoption`, QA 105/105 protocol |
| Минимум 8 потоков | да: `Threads` 1…16, 8 потоков проверены стрессом |
| Стабильность под нагрузкой | 120 циклов `go/stop/isready` на 8 потоках: 0 ошибок; 12 партий 2×8 потоков: 0 aborts |
| Syzygy | да, опционально: локальные `.rtbw`/`.rtbz`, WDL в поиске + DTZ на корне |
| Токен Lichess | только scope `bot:play`; `board:play` и остальные не нужны |

### Мини-дашборд

Локальная веб-страница для наблюдения за партиями:

```powershell
python tools\bot_dashboard.py --port 8765
```

Откройте <http://127.0.0.1:8765/>.

Показывает текущую партию с локальной доской (SVG из FEN), часы, рейтинги,
список ходов и последние результаты. Можно пролистать партию с начала:
`⏮ ◀ ▶ ⏭` или клик по ходу. Обновление каждые 5 секунд. Токен читается
из `C:\LichessBot\lichess-bot\config.yml` и в браузер не попадает; сервер
слушает только `127.0.0.1`. Подробности — [docs/DASHBOARD.md](docs/DASHBOARD.md).


## Структура проекта

```text
src/
  engine.hpp                 типы, Position, TT, Search, NNUE API
  bitboards.cpp              атаки, magic bitboards, Zobrist, initAll
  position.cpp               FEN, make/unmake, генерация ходов, SEE, perft
  eval.cpp                   классическая оценка, инференс NNUE, смешивание
  search.cpp                 TT, negamax/PVS, quiescence, время, info
  syzygy.cpp                 WDL/DTZ probe через Fathom
  uci.cpp                    UCI-цикл, опции, bench, perft, eval, d
  trainer.cpp                datagen / train / stats
  nnue_weights.cpp           скомпилированные веса (генерируются тренером)
  nnue_weights_placeholder.cpp  пустые веса для сборки без сети
build.ps1                    обычная сборка chessbot.exe + trainer.exe
build_pgo.ps1                PGO-release: bench-профиль + chessbot_pgo.exe
third_party/fathom/          MIT-библиотека Syzygy probe (tbprobe.c/h)
tools/
  arena.py                   мини-арена UCI (python-chess)
  puzzle_test.py             тест на тактических задачах Lichess
  puzzle_report.py           сравнение JSON-отчётов пазлов
  bot_dashboard.py           локальный дашборд партий Lichess-бота
  dashboard.js               front-end дашборда
  make_lichess_book.py       генератор Polyglot-книги из Lichess masters
  label_stockfish.py         разметка статической оценкой Stockfish
  sf19/                      Stockfish 19: тестовый артефакт, не часть движка
tests/
  uci_engine.py, testutil.py обёртка и общие помощники
  run_*.py                   perft/protocol/timing/robustness/selfplay
  run_stress.py              стресс 8 потоков, stop/isready, TB
  probe*.py                  точечные проверки
  REPORT.md                  сводный QA-отчёт
  logs/                      JSON/лог-отчёты прогонов
  pgn/                       self-play партии
docs/
  ARCHITECTURE.md            устройство проекта
  ALGORITHMS.md              таблица алгоритмов и параметров
  BENCHMARKS.md              измерения и оговорки
  PUZZLES.md                 методика теста на задачах Lichess
  SYZYGY.md                  Syzygy: опции, файлы, проверка
  DASHBOARD.md               локальный дашборд партий
  AUDIT.md                   аудит багов и план улучшений
  BOOK.md                    книга дебютов Polyglot
experiments/
  multithread/               Lazy SMP: исходники, отчёт, замеры
  multipv_build/             отдельная analysis-сборка с MultiPV/Ponder
  optimizations_2026-11-25/  PEXT/ProbCut/PGO: снимок src и матчи
  syzygy_2026-11-25/         Syzygy + 8 потоков: отчёт и снимок src
data_train.txt               self-play датасет (28 759 строк)
sf_data.txt                  Stockfish-разметка (65 443 строки)
nnue_*.cpp                   сгенерированные веса (артефакты)
*.exe                        локальные сборки (не гарантируется актуальность)
LICENSE, NOTICE.md           лицензия и сторонние компоненты
```

## Ограничения и roadmap

### Ограничения

- **Syzygy — только локальные файлы.** WDL-probe в поиске и DTZ-ход на корне
  реализованы; онлайн-проб и DTM нет. Без скачанных `.rtbw`/`.rtbz` эндшпили
  играются обычным поиском.
- **Lazy SMP есть, но MultiPV/Ponder в основную сборку не входят.** Многопоточный
  поиск включается опцией `Threads`; MultiPV/Ponder сохранены отдельно в
  `experiments/multipv_build` и `chessbot_multipv.exe`.
- **NNUE экспериментальна.** Маленькая сеть, без королевских бакетов, без
  инкрементального аккумулятора, без SIMD-квантизации; сила не доказана.
- **Классическая оценка проще, чем у топовых движков.** Нет многих терминов:
  связанные/отсталые пешки, угрозы, pin-мотивы, сложная king safety и т. д.
- **TT: одна запись на индекс, 32-битная сигнатура.** Возможны коллизии; нет
  кластеров и replacement policy уровня Stockfish.
- **Нет pondering, MultiPV, Chess960, `searchmoves`, opening book в основной
  MT-сборке.** MultiPV/Ponder есть только в отдельной analysis-сборке.
- **Ограниченное управление временем.** Базовые soft/hard лимиты, без сложной
  оценки стабильности и «умного» распределения.
- **Сборка ориентирована на Windows/MinGW.** MSVC-ветка CMake не тестировалась.
- **CI нет.** Есть скриптовый набор в `tests/` и логи прогонов, но нет
  автоматического CI и блокирующих регрессий. Свежий полный прогон — 358/358;
  исторический прогон до фиксов дал 351/358.

### Roadmap

1. Довести NNUE до измеримого прироста: корректный HalfKP с королевскими бакетами,
   аккумулятор, квантование, контролируемые матчи/SPRT, а не корреляция на val.
2. ~~Добавить Syzygy probe (WDL/DTZ)~~ — сделано через Fathom:
   `SyzygyPath`, WDL в поиске, DTZ на корне. Осталось DTM/online и полные
   6–7-piece наборы.
3. ~~Реализовать SMP (Lazy SMP)~~ — сделано, `Threads` 1…16, QA 358/358.
   Осталось измерить прирост в рейтинговых партиях и решить судьбу MultiPV/Ponder.
4. Улучшить оценку: больше пешечных/фигурных терминов, threat/pin/king safety,
   аккуратный tune.
5. Добавить MultiPV, pondering, Chess960, `searchmoves`.
6. Сделать переносимые сборки без `-march=native`, CI и набор perft/bench-регрессий;
   перепроверить robustness после недавних фиксов и поддерживать `tests/REPORT.md`
   в актуальном состоянии.
7. При желании — порт под MSVC/Clang-cl.

## Лицензия и Stockfish

Проект распространяется на условиях **GNU GPL v3** (GPL-3.0-or-later): см.
[LICENSE](LICENSE) и [NOTICE.md](NOTICE.md).

Коротко:

- `tools/sf19/stockfish/**` — полноценная копия Stockfish 19 (GPLv3), используемая
  только как тестовый/разметочный артефакт. Она не линкуется в `chessbot.exe` и не
  является частью движка. В релиз её можно не включать.
- `nnue_sf.cpp`, `sf_data.txt`, `sf_small.txt`, а также часть данных/весов в
  `experiments/nnue/` получены из выходов Stockfish 19 и должны распространяться
  на GPLv3-совместимых условиях.
- `src/nnue_weights.cpp` (residual-сеть) обучен на self-play метках проекта, но
  политика проекта — GPLv3 для всего репозитория, чтобы избежать юридической
  неопределённости.
- Полный текст GPLv3 и пояснения — в [LICENSE](LICENSE) и [NOTICE.md](NOTICE.md).

Это не юридическая консультация. Если вы хотите лицензировать оригинальный код
иначе, сначала удалите SF-производные артефакты и `tools/sf19`, затем проверьте
происхождение весов.

## Документация

- [QUICKSTART.md](QUICKSTART.md) — сборка и первый запуск за пять минут.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — модули, поток данных, структуры,
  инварианты, точки расширения.
- [docs/ALGORITHMS.md](docs/ALGORITHMS.md) — таблица реализованных алгоритмов,
  формулы и параметры.
- [docs/BENCHMARKS.md](docs/BENCHMARKS.md) — perft, bench, обучение NNUE и матчи,
  с явными оговорками.
- [docs/PUZZLES.md](docs/PUZZLES.md) — тест на тактических задачах Lichess:
  методика, команды, ограничения.
- [docs/SYZYGY.md](docs/SYZYGY.md) — Syzygy tablebases: опции, файлы, примеры,
  ограничения и многопоточность.
- [docs/DASHBOARD.md](docs/DASHBOARD.md) — локальный дашборд партий.
- [docs/AUDIT.md](docs/AUDIT.md) — аудит багов и план улучшений.
- [docs/BOOK.md](docs/BOOK.md) — Polyglot-книга дебютов: генерация, проверка,
  статус интеграции.
- [NOTICE.md](NOTICE.md) — сторонние компоненты и лицензионные вопросы.
