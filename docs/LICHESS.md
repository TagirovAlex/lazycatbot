# Публикация ChessBot на Lichess

Движок сам не подключается к Lichess: нужен bot-аккаунт и процесс
[`lichess-bot`](https://github.com/lichess-bot-devs/lichess-bot), который
передаёт позиции и часы в UCI-движок.

## 1. Требования

- **Отдельный новый аккаунт Lichess.** Не используйте основной: upgrade в BOT
  необратим, и аккаунт не должен иметь сыгранных партий до upgrade.
- OAuth-токен со scope **`bot:play`**.
- Постоянно работающий компьютер/VPS и стабильный интернет.
- Для команды
  [Leaderboard of BOTs](https://lichess.org/team/leaderboard-of-bots):
  - ≥ 1 рейтинговая партия в неделю в соответствующем контроле;
  - ≥ 50 рейтинговых партий в этом контроле;
  - рейтинг без `?` (не provisional);
  - RD < 75 в standard (и < 65 в вариантах);
  - отсутствие нарушений ToS/банов и «фарма» слабых ботов.

## 2. Создание BOT-аккаунта

1. Зарегистрируйте новый аккаунт Lichess, не играйте им.
2. Создайте токен:
   <https://lichess.org/account/oauth/token/create?scopes[]=bot:play&description=ChessBot>

   Нужен **только scope `bot:play`** («Play games with the bot API» /
   «Играть партии с API ботов»). Не выбирайте `board:play` — это Board API
   для физических досок, не для бота. `challenge:read`/`challenge:write`,
   `engine:read`/`engine:write`, `puzzle:*`, `study:*`, `tournament:*`,
   `team:*`, `msg:write`, `email:read`, `preference:*` для игры бота не нужны.
   Токен показывается один раз; храните его в `config.yml` или переменной
   `LICHESS_BOT_TOKEN` и не коммитьте.

3. Сохраните токен. Upgrade (необратимо):

   ```bash
   curl -d "" https://lichess.org/api/bot/account/upgrade \
     -H "Authorization: Bearer <TOKEN>"
   ```

   Либо после установки `lichess-bot`:

   ```bash
   python lichess-bot.py -u
   ```

4. Войдите этим аккаунтом на Lichess, откройте команду
   [Leaderboard of BOTs](https://lichess.org/team/leaderboard-of-bots) и
   отправьте заявку на вступление.

## 3. Установка lichess-bot

```powershell
git clone https://github.com/lichess-bot-devs/lichess-bot
cd lichess-bot
python -m pip install -r requirements.txt
```

Скопируйте `lichess/config.yml.example` из этого репозитория в
`lichess-bot/config.yml`, впишите токен и абсолютные пути. Пример уже настроен
на стандартные шахматы, `Threads: 2`, отсутствие ponder/книги по умолчанию.

Запуск:

```powershell
python lichess-bot.py
```

Для проверки без игры:

```powershell
python lichess-bot.py --help
```

## 4. Настройки движка

В `config.yml`:

```yaml
engine:
  dir: "<path-to-chessbot>/"
  name: "chessbot.exe"
  working_dir: "<path-to-chessbot>/"
  protocol: "uci"
  ponder: false

  uci_options:
    Hash: 256
    Threads: 2
    Move Overhead: 150
    UseNNUE: false
    NNUEWeight: 25
    NNUEClip: 80
    # SyzygyPath: "C:/syzygy/3-4-5"
    # SyzygyProbeDepth: 1
    # Syzygy50MoveRule: true
    # SyzygyProbeLimit: 7
```

- `ponder: false` — в основной MT-сборке полноценного ponder нет. Ponder есть
  только в отдельной analysis-сборке `chessbot_multipv.exe`.
- `Threads` работает через Lazy SMP: 2 потока — это main + 1 helper. Поддержано
  до 16 потоков, 8 проверены стресс-тестом; начинайте с 2–4 на общем CPU и
  проверяйте стабильность.
- `SyzygyPath` опционален: локальные `.rtbw`/`.rtbz` дают perfect play в
  эндшпилях. Файлы не входят в репозиторий; см. `docs/SYZYGY.md`.
- `Hash: 256` достаточно; не ставьте больше, чем есть свободной RAM.
- `UseNNUE: false` — текущая модель по матчам не сильнее классики. Включайте
  только после собственных измерений.
- `Move Overhead: 150` компенсирует сетевую задержку; `move_overhead` в
  конфиге lichess-bot можно поставить 500–1000 мс.

## 5. Режимы и матчмейкинг

Чтобы получить 50 рейтинговых партий и снизить RD:

```yaml
challenge:
  concurrency: 1
  accept_bot: true
  only_bot: false
  variants:
    - standard
  time_controls:
    - bullet
    - blitz
    - rapid
  modes:
    - rated
    - casual
  max_base: 600
  max_increment: 10
  max_simultaneous_games_per_user: 1

matchmaking:
  allow_matchmaking: true
  challenge_timeout: 30
  challenge_initial_time: [60, 180]
  challenge_increment: [1, 2]
  opponent_rating_difference: 250
  challenge_mode: rated
  rating_preference: "none"
```

Не устраивайте «фарм»: не вызывайте постоянно слабых ботов, не играйте
преимущественно со своими вторыми аккаунтами/соседними ботами. Это прямо
запрещено правилами команды.

## 6. Хостинг

- Windows: отключите сон/гибернацию, поставьте автозапуск через Планировщик
  заданий или NSSM; используйте стабильный Ethernet/Wi-Fi.
- VPS: 2–4 vCPU, 1–2 ГБ RAM, стабильная сеть; GPU не нужен, если NNUE
  выключена (наш инференс сейчас CPU).
- Токен храните только в локальном `config.yml`; не коммитьте его.
- Следите за `logs/` и `lichess-bot`; при падении движка перезапускайте процесс.
- Проверьте, что ноутбук не уходит в сон: иначе бот будет offline, а недельная
  активность потеряется.

### Если нет VPS и постоянно включённого ПК

Полноценный лидерборд требует не «24/7 любой ценой», но бот должен быть
**онлайн достаточно часто**, чтобы набрать 50 рейтинговых партий, держать
RD < 75 и играть хотя бы одну рейтинговую партию в неделю. Lichess не хостит
ботов и не играет за вас: пока процесс `lichess-bot` выключен, Challenges и
матчмейкинг не приходят.

Варианты без дорогого VPS:

- **Домашний ПК по расписанию.** Запускайте бота на 2–4 часа в удобное время;
  этого может хватить для 50 партий за несколько недель. Но рейтинг будет
  расти медленнее, а пропущенная неделя выкинет из недельного лидерборда.
- **Бесплатный облачный tier.** Oracle Cloud Always Free (ARM Ampere) —
  хороший вариант для Linux-бота; также подойдут бесплатные лимиты других
  облаков, если они не усыпляют процесс.
- **Raspberry Pi / старый мини-ПК / Android через Termux** как дешёвый
  всегда-включённый хост. Нужна сборка под ARM и стабильный интернет.
- **Дешёвый VPS за €3–5/мес** — самый простой путь к стабильности.

Но «топ» — это не только онлайн. В топе боты с рейтингом ~3000+, сильной
NNUE и постоянной активностью. Наш нативный движок на 2300–2450 может
попасть в лидерборд при выполнении формальных требований, но не в верхние
строчки. Без постоянного хоста и без Stockfish-класса движка рассчитывать
на топ-1/топ-10 не стоит.

## 7. Честность и лицензия

- BOT-аккаунт не должен использоваться для помощи людям в обычных партиях.
- Не манипулируйте рейтингом, не создавайте «фермы» ботов и не обходите лимиты.
- Наш проект — GPL-3.0-or-later; `lichess-bot` — AGPL-3.0-or-later, запускается
  отдельным процессом. Укажите в профиле бота, что это открытый движок.
- Если используете Stockfish/его веса, соблюдайте GPL и явно указывайте это.

## 8. Чего ждать по рейтингу

Нативный `chessbot.exe` на 80–100 мс/ход и 1–4 потоках ориентировочно
**2300–2450 Lichess bot Elo**, не 3000. Это следует из 100-игровых матчей
против Stockfish 19 `UCI_Elo=2400` (46–47.5%, CI −95…+59), а не из точного
рейтинга. Для 3000 нужен Stockfish-класс движка; не заявляйте 3000 без
матчей. Lichess-рейтинг и наш локальный `UCI_Elo` — разные шкалы.
