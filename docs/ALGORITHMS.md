# Алгоритмы и оптимизации

Таблица отражает фактическое состояние `src/` на 2026-11-24. Параметры выписаны из
кода, а не из общих описаний похожих движков. Если параметр не указан, он зашит
константой в соответствующем файле.

Обозначения: **да** — реализовано и используется; **эксп.** — реализовано, но
экспериментально; **нет** — отсутствует.

## 1. Доска, правила, инфраструктура

| Компонент | Статус | Детали | Файл |
|---|---|---|---|
| Битборды | да | `byType[6]`, `byColor[2]`, `occ`; `board[64]` как mailbox | `engine.hpp`, `position.cpp` |
| Magic bitboards | да | Runtime-генерация, фиксированные seed; rook 4096, bishop 1024 entries на поле | `bitboards.cpp` |
| Атаки коня/короля/пешки | да | Предрасчитанные таблицы | `bitboards.cpp` |
| `between` / `line` | да | Таблицы для линий и промежутков | `bitboards.cpp` |
| Zobrist | да | `psq[12][64]`, `castling[16]`, `epFile[8]`, `side`; инкрементально | `bitboards.cpp`, `position.cpp` |
| En passant в хеше | да, условно | Только если `epHashable()` — есть пешка, способная взять | `position.cpp` |
| FEN | да | Полная валидация: 32 фигуры, один король на цвет, ≤8 пешек, неверная сторона/рокировка/шах отклоняются | `position.cpp` |
| Генерация ходов | да | Псевдолегальная; легальность через `makeMove` | `position.cpp` |
| Рокировка | да | Проверка пустых полей, ладьи, битых полей короля | `position.cpp` |
| Превращения | да | Все четыре фигуры, включая взятия и невзяточные превращения в qsearch | `position.cpp` |
| SEE | да | Рекурсивная, MVV-подобная, с обновлением occupancy | `position.cpp` |
| Perft | да | Легальные ходы, рекурсивный | `position.cpp` |
| Ничья 50 ходов | да | `halfMove >= 100` | `position.cpp` |
| Повторение | да | Сравнение ключей через 2 полухода в пределах halfmove | `position.cpp` |
| Недостаточный материал | да | K vs K, K+minor, позиции без коней/пешек/ладей/ферзей, где все слоны на полях одного цвета | `position.cpp` |
| Полный FIDE dead-position | нет | Не анализируются все «мёртвые» позиции | — |
| Chess960 | нет | Только стандартные рокировки | — |

## 2. Поиск

| Алгоритм/эвристика | Статус | Параметры и условия | Файл |
|---|---|---|---|
| Итеративное углубление | да | `depth = 1..MAX_PLY-2` | `search.cpp` |
| Aspiration windows | да | С глубины 4, окно `prev ± 30`, delta удваивается; сброс в полное окно при delta > 4000 | `search.cpp` |
| PVS | да | Первый ход — полное окно, остальные — нулевое; re-search при улучшении | `search.cpp` |
| Transposition table | да | 16 байт, 32-битная сигнатура, aging, bound, depth-aware замена, mate correction | `search.cpp` |
| TT cutoff | да | Только не-PV и `tte.depth >= depth`; EXACT/LOWER/UPPER | `search.cpp` |
| Null-move pruning | да | `depth >= 3`, не root/PV/check, `staticEval >= beta`, есть непешечный материал, `histPly < MAX_HIST-1`, успешный `makeNullMove()`, `hasLegalMove()`; `R = 2 + depth/4 + min(3, (eval-beta)/200)` | `search.cpp` |
| Reverse futility | да | Не root/PV/check, `depth <= 6`, `staticEval - 85*depth >= beta`, `hasLegalMove()` | `search.cpp` |
| Razoring | да | Не root/PV/check, `depth <= 2`, `staticEval + 300 + 180*depth <= alpha`, `hasLegalMove()` → qsearch | `search.cpp` |
| Futility pruning | да | Тихий ход, `depth <= 4`, `staticEval + 90 + 90*depth <= alpha`, после хотя бы одного легального | `search.cpp` |
| Late Move Pruning | да | Тихий ход, `depth <= 5`, `legal >= 5 + depth*depth` | `search.cpp` |
| SEE pruning | да | Взятие, `depth <= 6`, `see < -110*depth` | `search.cpp` |
| LMR | да | `R = int(0.75 + ln(d)*ln(m)/2.25)`, условия `depth>=3`, `legal>3`, тихий, не check, не PV; `+1` для cutNode, `+1` если не improving; cap `newDepth-1` | `search.cpp` |
| Check extension | да | `if (inCheck) ++depth` в root и negamax | `search.cpp` |
| IIR | да | `depth >= 5` и нет TT-хода → `--depth` | `search.cpp` |
| Killers | да | 2 на ply, обновление при quiet beta-cutoff | `search.cpp` |
| History heuristic | да | `history_[color][from][to] += depth*depth`; очистка при > 2^19 | `search.cpp` |
| Countermove | да | `counterMoves_[side][prevFrom][prevTo]`, score 80k после killers; обновление на quiet beta-cutoff при `ply > 0` | `search.cpp` |
| Capture history | да | `captureHistory_[side][attackerType][to][victimType]`; бонус в ordering до 8000, обновление на capture beta-cutoff | `search.cpp` |
| MVV-LVA ordering | да | `100000 + 16*victim - attacker`, плюс capture history; TT move 1e6, killers 90k/89k, countermove 80k | `search.cpp` |
| Quiescence | да | Stand-pat, captures + promotions, evasions в шахе | `search.cpp` |
| Delta pruning | да | `standPat + victim + 180 < alpha` | `search.cpp` |
| SEE в qsearch | да | Пропуск взятий с SEE < 0, кроме первого легального; en passant не отсекается по SEE | `search.cpp` |
| Mate-distance pruning | да | `alpha >= -MATE+ply`, `beta <= MATE-ply-1` | `search.cpp` |
| Syzygy WDL | да | `tb_probe_wdl` при `depth >= SyzygyProbeDepth`, `halfMove == 0` (если включено правило 50); score ±20000 | `syzygy.cpp` |
| Syzygy root DTZ | да | `tb_probe_root` только в главном потоке; готовый ход, `info depth 0` | `syzygy.cpp` |
| Root legal filter | да | `makeMove`/`unmakeMove` для каждого корневого хода | `search.cpp` |
| Root stable sort | да | После каждой глубины, по score | `search.cpp` |
| Fallback move | да | Первый легальный ход, если поиск прерван до глубины 1 | `search.cpp` |
| Time management | да | Soft/hard, `Move Overhead`, `movestogo` по умолчанию 30 | `search.cpp`, `uci.cpp` |
| Node limit | да | `go nodes`; проверка каждые 1024 узла | `search.cpp` |
| Pondering | только analysis-сборка | `ponder`/`ponderhit` работают в `chessbot_multipv.exe`; в MT-сборке игнорируются | `uci.cpp` |
| MultiPV | только analysis-сборка | 1..8 в `experiments/multipv_build`; в MT-сборке нет | `search.cpp` |
| Lazy SMP | да | `Threads` 1…16, helpers + lockless TT, общий stop | `search.cpp`, `uci.cpp` |
| Singular extensions | нет | — | — |
| Capture/continuation history | Capture history — да, continuation — нет | Capture history описана выше; continuation/follow-up истории нет | `search.cpp` |
| Correction history | нет | — | — |

### Порядок отсечений в negamax

Упрощённо:

```text
draw / mate-distance
qsearch if depth <= 0
check extension
TT probe + TT cutoff
static eval + improving
reverse futility
razoring
null move
IIR
move generation + ordering
for each move:
    futility / LMP / SEE pruning (after first legal)
    PVS + LMR
    update killers/history on cutoff
TT store
```

## 3. Оценка

| Термин | Статус | Детали | Файл |
|---|---|---|---|
| Материал + PST | да | Отдельные mg/eg значения; PeSTO-подобные масштабы | `eval.cpp` |
| Tapered eval | да | `phase`: N/B=1, R=2, Q=4, max 24 | `eval.cpp` |
| Мобильность | да | N 4/3, B 3/4, R 2/3, Q 1/1 (mg/eg) за атакованное поле | `eval.cpp` |
| Пара слонов | да | +30 mg / +45 eg | `eval.cpp` |
| Сдвоенные пешки | да | −12 mg / −20 eg за каждую лишнюю | `eval.cpp` |
| Изолированные пешки | да | −15 mg / −12 eg за пешку | `eval.cpp` |
| Проходные пешки | да | Бонус по относительной горизонтали: mg `0,5,10,20,35,60,100,0`; eg `0,10,20,35,60,100,160,0` | `eval.cpp` |
| Защищённый проходник | да | +5 mg / +8 eg, если впереди есть своя пешка-защитник | `eval.cpp` |
| Ладья на открытой линии | да | +25 mg / +15 eg | `eval.cpp` |
| Ладья на полуоткрытой | да | +12 mg / +8 eg | `eval.cpp` |
| Ладья на 7-й | да | +15 mg / +25 eg | `eval.cpp` |
| Коневой форпост | да | Отн. горизонт 3–5, своя пешечная поддержка, нет вражеской пешечной атаки: +18/+12 | `eval.cpp` |
| Пешечный щит | да | +14 / +7 / −10 за пешку впереди/двумя/ничего | `eval.cpp` |
| Открытая линия у короля | да | −18 mg | `eval.cpp` |
| Давление на королевское кольцо | да | Вес 2/3/5 (лёгкая/ладья/ферзь), умножается на 4 | `eval.cpp` |
| Пространство | да | +2 mg за свою фигуру на c–f в центральной зоне | `eval.cpp` |
| Mop-up | да | При `phase <= 6` и материальном перевесе > 300 | `eval.cpp` |
| Темп | да | +10 cp | `eval.cpp` |
| Угрозы/pins/связки | нет | — | — |
| Пешечные острова/отсталые | нет | Есть только сдвоенные/изолированные/проходные | — |
| King pawn storm | нет | Упрощённая king safety | — |

Оценка считается с точки зрения стороны хода. Классическая часть включает темп;
NNUE-поправка добавляется после неё.

## 4. NNUE

| Компонент | Статус | Детали | Файл |
|---|---|---|---|
| Сеть | эксп. | 768 → 64 → 1, ReLU, float | `eval.cpp`, `nnue_weights.cpp` |
| Признаки | эксп. | 12 относительных типов × 64 поля; без королевских бакетов | `eval.cpp` |
| Инкрементальный аккумулятор | нет | Признаки пересчитываются на каждой оценке | — |
| Квантизация/SIMD | нет | Float-инференс | — |
| Residual-обучение | эксп. | Цель — `search_score − classical`, clamp ±800 cp | `trainer.cpp` |
| Direct-обучение | эксп. | Цель — внешняя оценка, clamp ±1500 cp | `trainer.cpp` |
| Смешивание | да | Residual: `base + net*w/100`; direct: `(net*w + base*(100-w))/100` | `eval.cpp` |
| UCI-выключатель | да | `UseNNUE`, `NNUEWeight` | `uci.cpp` |
| Runtime-загрузка `.nnue` | нет | Веса компилируются в бинарник | — |
| SPRT-подтверждение силы | нет | Быстрые матчи дали отрицательный результат | — |

### Данные и тренер

| Режим | Команда | Что делает |
|---|---|---|
| datagen | `trainer datagen <games> <depth> <out> [seed]` | Self-play, метки — оценка поиска; 8% случайных ходов, первые 8 plies случайны; max 220 plies |
| train | `trainer train <data> <epochs> <out.cpp> [direct]` | 90/10 split, Adam, batch 64, lr 0.003×0.97^epoch, early stop 8 |
| stats | `trainer stats <data> [direct]` | RMSE классической оценки, correlation, bias, диапазоны |

Формат строки: `FEN|score`; `score` — с точки зрения стороны хода. Матовые оценки
отбрасываются.

## 5. UCI и инфраструктура

| Компонент | Статус | Детали |
|---|---|---|
| UCI-цикл | да | `uci`, `isready`, `ucinewgame`, `position`, `go`, `stop`, `quit`, `setoption` |
| Асинхронный поиск | да | main worker + helpers, `stopFlag` и общий `SharedSearchState` |
| `Hash` | да | 1…4096 МБ, TT пересоздаётся |
| `Move Overhead` | да | 0…5000 мс |
| `UseNNUE` | да | `true`/`false`; по умолчанию `false` |
| `NNUEWeight` | да | 0…200, по умолчанию 25 |
| `NNUEClip` | да | 0…1500, по умолчанию 80 |
| `SyzygyPath` | да | строка; `.rtbw`/`.rtbz`, несколько путей через `;` |
| `SyzygyProbeDepth` | да | 1…100, по умолчанию 1 |
| `Syzygy50MoveRule` | да | check, по умолчанию true |
| `SyzygyProbeLimit` | да | 0…7, по умолчанию 7 |
| `Clear Hash` | да | Очистка TT + истории |
| `Threads` | да | 1…16, Lazy SMP; по умолчанию 1 |
| `d` | да | ASCII-доска + FEN |
| `eval` | да | classical/neural/total + флаги |
| `perft` | да | `perft <depth>` |
| `bench` | да | 6 FEN, default depth 10 |
| Многопоточный поиск | да | Lazy SMP, `Threads` 1…16; 8 потоков проверены стрессом |
| Syzygy | да | WDL в поиске + DTZ на корне; локальные файлы |
| Opening book | нет | — |
| Ponder | только analysis-сборка | `chessbot_multipv.exe` |
| MultiPV | только analysis-сборка | `chessbot_multipv.exe` |
| Chess960 | нет | — |
| `searchmoves` | нет | — |

## 6. Ключевые формулы

### LMR

```text
R = int(0.75 + ln(depth) * ln(moveNumber) / 2.25)
```

Применяется к тихим ходам при `depth >= 3`, `moveNumber > 3`, вне шаха и не на PV.
Дополнительно:

```text
if (cutNode) ++R;
if (!improving) ++R;
R = clamp(R, 0, newDepth - 1);
```

### Null move

```text
R = 2 + depth / 4 + min(3, (staticEval - beta) / 200)
score = -negamax(depth - 1 - R, -beta, -beta + 1, ...)
```

Требуется `depth >= 3`, не root/PV/check, `staticEval >= beta`, наличие непешечного
материала, `histPly < MAX_HIST-1`, успешный `makeNullMove()` и `hasLegalMove()`.
`isNullHist_` исключает null-move из повторений.

### Reverse futility / razoring / futility

```text
RFP:      staticEval - 85 * depth >= beta          (depth <= 6)
Razoring: staticEval + 300 + 180 * depth <= alpha  (depth <= 2)
Futility: staticEval + 90 + 90 * depth <= alpha    (depth <= 4, quiet)
LMP:      legal >= 5 + depth * depth               (depth <= 5, quiet)
SEE:      see < -110 * depth                       (depth <= 6, capture)
```

### Aspiration

```text
depth >= 4: [prevScore - 30, prevScore + 30]
fail low:  alpha -= delta; delta *= 2
fail high: beta  += delta; delta *= 2
delta > 4000: полное окно [-INF, INF]
```

### Время

```text
movetime: reserve = min(overhead, max(1, movetime / 10))
          soft = hard = max(1, movetime - reserve)

clock:
  mtg  = movestogo > 0 ? movestogo : 30
  base = time / mtg + (inc / 4) * 3
  soft = clamp(base, 3, time / 2)
  hard = min(max(3, (time / 4) * 3), soft * 4 + 20)
  soft = max(1, soft - overhead)
  hard = max(soft, hard - overhead)
```

Мягкий лимит проверяется после глубины, жёсткий — в узлах (каждые 1024) и после
глубины 8 при `elapsed * 2 > hard`.

### Матовые оценки в TT

```text
scoreToTT:   score >= MATE_IN_MAX ? score + ply : (score <= -MATE_IN_MAX ? score - ply : score)
scoreFromTT: score >= MATE_IN_MAX ? score - ply : (score <= -MATE_IN_MAX ? score + ply : score)
```

## 7. Что не реализовано (сознательно)

- Syzygy/Гавраше tablebases: WDL/DTZ локально сделаны; нет онлайн-проб и DTM.
- SMP/Lazy SMP — сделано в основной сборке (`Threads`); pondering и MultiPV
  остались отдельной analysis-сборкой.
- Singular extensions, probation, continuation history, correction history.
- NNUE-аккумулятор, квантизация, SIMD, королевские бакеты.
- Кластерный TT, bucket replacement, 64-битная сигнатура.
- MultiPV и pondering — только в отдельной analysis-сборке.
- Opening book, online-базы, анализ «мёртвых» позиций FIDE.
- Динамическое управление временем на основе стабильности оценки.
- CI/SPRT-инфраструктура.

## 8. Известные особенности реализации

- `makeNullMove()` возвращает `bool`; при заполненной истории null-move не делается,
  а `isNullHist_` не даёт повторению считать null-move реальным ходом.
- `hasLegalMove()` используется как защита RFP/razoring/null-move от ложных
  отсечений в позициях без легальных ходов.
- Проверка легальности выполняется через `makeMove`, поэтому perft медленнее
  «чистого» bulk-perft.
- `bench` использует текущую TT, а не свежую; для чистых замеров нужен новый
  процесс.
- `go` без лимитов уходит в `infinite`; `go ponder` не включает pondering.
- Пустая/невалидная позиция теперь защищена: `UciEngine` ставит startpos,
  `go` проверяет `kingSq`, а `inCheck`/`isSquareAttacked` имеют guard'ы. Это
  исправлено после исторического QA-прогона (см. `tests/REPORT.md`).
- При малом времени управление часами консервативно; для `go movetime` теперь
  резервируется только ~10% бюджета вместо полного `Move Overhead`, поэтому
  100 мс дают около 90 мс поиска.
- При мгновенном `quit` после `go` поиск может не успеть завершить первую глубину,
  и будет возвращён запасной ход.
- `UseNNUE` по умолчанию `false` (в весах стоит `enabled = false`); это осознанно,
  потому что в парных матчах сеть не дала прироста.
- `NNUEClip` ограничивает модуль сетевой поправки; при включении сети используйте
  небольшой вес (10–25) и клип 50–80.

См. также [ARCHITECTURE.md](ARCHITECTURE.md) и [BENCHMARKS.md](BENCHMARKS.md).
