# Syzygy tablebases

Движок поддерживает локальные Syzygy-таблицы через [Fathom](https://github.com/jdart1/Fathom)
(MIT, `third_party/fathom/`). Файлы `.rtbw`/`.rtbz` в репозиторий не входят.

## Что реализовано

| Возможность | Статус |
|---|---|
| WDL-probe в поиске | да, `tb_probe_wdl`, потоки-безопасно |
| DTZ-probe на корне | да, `tb_probe_root`, только главный поток |
| Учёт правила 50 ходов | да, `Syzygy50MoveRule` |
| Ограничение числа фигур | да, `SyzygyProbeLimit` |
| Минимальная глубина probe | да, `SyzygyProbeDepth` |
| Несколько каталогов | да, через `;` в `SyzygyPath` |
| Онлайн-проба через Lichess API | нет |
| DTM-таблицы | нет, только WDL/DTZ |

Когда таблицы не подключены или позиция не покрыта, движок работает как обычно.
Стоимость выключенной поддержки — одна предсказуемая проверка на узел.

## UCI-опции

```text
option name SyzygyPath type string default <empty>
option name SyzygyProbeDepth type spin default 1 min 1 max 100
option name Syzygy50MoveRule type check default true
option name SyzygyProbeLimit type spin default 7 min 0 max 7
```

Пример:

```text
setoption name SyzygyPath value C:/syzygy/3-4-5
setoption name SyzygyProbeLimit value 5
setoption name SyzygyProbeDepth value 1
setoption name Syzygy50MoveRule value true
isready
```

После установки пути движок печатает:

```text
info string Syzygy: loaded 5-piece tablebases from "..."
```

Если файлов нет:

```text
info string Syzygy: no tablebase files found in "..."
```

## Где взять файлы

Официальные Syzygy-файлы:

- WDL: <https://tablebase.lichess.ovh/tables/standard/3-4-5-wdl/>
- DTZ: <https://tablebase.lichess.ovh/tables/standard/3-4-5-dtz/>

Для проверки хватает нескольких файлов:

```powershell
New-Item -ItemType Directory -Force data\syzygy | Out-Null
foreach ($n in @("KQvK","KRvK","KPvK","KQvKR","KQvKP","KRvKP","KPvKP")) {
  curl.exe -sL "https://tablebase.lichess.ovh/tables/standard/3-4-5-wdl/$n.rtbw" -o "data\syzygy\$n.rtbw"
  curl.exe -sL "https://tablebase.lichess.ovh/tables/standard/3-4-5-dtz/$n.rtbz" -o "data\syzygy\$n.rtbz"
}
```

Для игры без ошибок в эндшпилях нужен полный набор 3-4-5 (а при желании — 6-7),
но это уже гигабайты. Начинайте с 3-4-5 WDL+DTZ.

## Примеры

### KQvK

```text
position fen 7k/8/8/8/8/8/8/KQ6 w - - 0 1
go movetime 100
```

Ожидаемый вывод — мгновенный корневой probe:

```text
info depth 0 seldepth 0 score cp 20000 nodes 0 nps 0 hashfull 0 time 0 pv b1g1
bestmove b1g1
```

Lichess tablebase для этой позиции тоже даёт `b1g1` (DTZ 13).

### KQvKR

```text
position fen 7k/8/8/8/8/8/6r1/KQ6 w - - 0 1
go movetime 100
```

Ответ: `b1h1` (`Qh1+`), DTZ 3 — совпадает с Lichess tablebase.

### Проверка WDL в поиске

Если поставить `SyzygyProbeLimit=3`, корень KQvKR (4 фигуры) не будет взят
из таблиц, но после взятия ладьи поиск увидит KQvK и вернёт TB-оценку:

```text
setoption name SyzygyProbeLimit value 3
position fen 7k/8/8/8/8/8/6r1/KQ6 w - - 0 1
go depth 8
```

В PV появится `h1g2` и оценка около `cp 19997`.

### Правило 50 ходов

```text
position fen 7k/8/8/8/8/8/8/KQ6 w - - 99 1
```

При `Syzygy50MoveRule=true` оценка будет `cp 0` (ничья по 50 ходам),
при `false` — `cp 20000`.

## Многопоточность и стабильность

- Fathom `tb_probe_wdl` потокобезопасен; его вызывают все Lazy SMP-воркеры.
- `tb_probe_root` не потокобезопасен. Его вызывает только главный поток,
  а доступ сериализован `std::shared_mutex`: WDL-пробы могут идти параллельно,
  корневая проба получает эксклюзивный доступ.
- Смена `SyzygyPath` останавливает и присоединяет все поисковые потоки до
  `tb_init`/`tb_free`.
- Стресс-тест: `python tests/run_stress.py --cycles 120 --threads 8 --movetime 40`
  (включая root и node probe) — 0 ошибок, 0 таймаутов, 0 нелегальных ходов.

## Ограничения

- Нет онлайн-проб: только локальные файлы.
- Нет DTM; на корне выбирается DTZ-ход, который может быть не самым «человеческим».
- Позиции с правами рокировки не берутся из таблиц (Fathom их не поддерживает;
  на практике это не эндшпильные позиции).
- WDL-probe в поиске используется только при `halfMove == 0`, если включено
  правило 50 ходов. Иначе оценка может быть неверной.
- Полный набор 6-7 фигур требует много диска и RAM; движок читает файлы
  по мере необходимости, но ОС-кэш и скорость диска важны.

## Лицензия

Fathom — MIT. Текст лицензии: `third_party/fathom/LICENSE`.
Сами таблицы распространяются отдельно; проверяйте условия источника.
