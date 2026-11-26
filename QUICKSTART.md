# Быстрый старт

Короткая инструкция для Windows. Полные сведения — в [README.md](README.md).

## 1. Проверить компилятор

Нужен MinGW-w64 `g++` с поддержкой C++20:

```powershell
g++ --version
where.exe g++
```

Если команда не найдена, установите MSYS2/MinGW-w64 (UCRT64) и добавьте
`C:\msys64\ucrt64\bin` в `PATH`.

## 2. Собрать

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

Появятся `chessbot.exe` и `trainer.exe`. Альтернативы:

```powershell
# CMake
cmake -S . -B build -G "MinGW Makefiles"
cmake --build build -j

# Makefile
mingw32-make -j
```

При сборке через CMake бинарники лежат в `build\`; при `build.ps1`/Makefile — в корне.

## 3. Запустить движок

```powershell
.\chessbot.exe
# для CMake:
# .\build\chessbot.exe
```

> В текущем `src` после запуска позиция уже startpos; `ucinewgame` тоже сбрасывает
> в startpos. Если используете старый бинарник, сначала отправьте `position startpos`.

В stdin введите:

```text
uci
isready
position startpos
go depth 10
```

Дождитесь `bestmove ...` и завершите:

```text
quit
```

> Не отправляйте `quit` одновременно с `go` через пайп: поиск будет остановлен, и
> движок вернёт запасной ход. Всегда дожидайтесь `bestmove`.

## 4. Проверить perft

```text
position startpos
perft 5
```

Ожидаемый результат: `perft 4865609 ...`.

Для Kiwipete:

```text
position fen r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1
perft 4
```

Ожидаемый результат: `perft 4085603 ...`.

## 5. Запустить bench

```text
position startpos
bench 10
```

Пример вывода на Ryzen 7 5800H (один поток, Hash 64 МБ, PGO-сборка):

```text
bench nodes 961335 time 670 ms nps 1434828
```

Точные цифры зависят от CPU и сборки; это не рейтинг силы.

## 6. Классический режим (рекомендуется для игры)

```text
setoption name UseNNUE value false
setoption name Hash value 256
isready
```

Нейросеть в проекте экспериментальная; по умолчанию она может быть включена
скомпилированными весами. Для игры надёжнее классическая оценка.

## 7. Матч против Stockfish 19

Нужен Python 3 и `python-chess`:

```powershell
pip install chess
python tools\arena.py --games 20 --movetime 100 `
  --engine-a .\chessbot.exe `
  --engine-b .\tools\sf19\stockfish\stockfish-windows-x86-64-universal.exe `
  --option-b UCI_LimitStrength=true --option-b UCI_Elo=1800
```

`tools/sf19` — тестовый артефакт, не часть движка. На малой выборке результат арены
имеет большую погрешность.

## 8. Обучение сети (опционально)

```powershell
# Генерация self-play данных
.\trainer.exe datagen 300 6 data_train.txt 20261123

# Residual-обучение
.\trainer.exe train data_train.txt 35 src\nnue_weights.cpp

# Пересборка с новыми весами
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

Подробности и честные результаты — в [docs/BENCHMARKS.md](docs/BENCHMARKS.md).
