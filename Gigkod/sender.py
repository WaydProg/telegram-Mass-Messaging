"""Одноразовая публикация объявления Gigkod в согласованных Telegram-группах."""

import argparse
import asyncio
import os
import re
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CHAT_LINK = re.compile(
    r"(?:@|https://t\.me/)([a-z][a-z0-9_]{3,31})/?", re.IGNORECASE | re.ASCII
)


def load_targets(path):
    targets = set()
    for line_no, line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), 1
    ):
        link = line.split("#", 1)[0].strip()
        if not link:
            continue
        match = CHAT_LINK.fullmatch(link)
        if match is None:
            raise ValueError(
                f"{path.name}, строка {line_no}: нужна ссылка на публичную группу или @username"
            )
        targets.add(match[1].lower())
    return sorted(targets)


def open_journal(path):
    db = sqlite3.connect(path)
    with db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS deliveries (
                campaign TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                message_id INTEGER,
                PRIMARY KEY (campaign, chat_id)
            )
        """)
    return db


async def deliver(client, args, targets, message):
    from telethon import errors, types

    with closing(open_journal(args.journal)) as db:
        failures = 0
        for username in targets:
            url = f"https://t.me/{username}"
            try:
                chat = await client.get_entity(f"@{username}")
            except (
                ValueError,
                errors.UsernameInvalidError,
                errors.UsernameNotOccupiedError,
            ):
                print(f"Пропуск {url} — адрес не найден", file=sys.stderr)
                failures += 1
                continue
            if not isinstance(chat, types.Channel) or not chat.megagroup or chat.left:
                print(
                    f"Пропуск {url} — требуется публичная группа, в которой вы состоите",
                    file=sys.stderr,
                )
                failures += 1
                continue
            if chat.forum:
                print(
                    f"Пропуск {url} — для группы с темами опубликуйте пост в нужной теме вручную",
                    file=sys.stderr,
                )
                failures += 1
                continue

            key = (args.campaign, chat.id)
            with db:
                claim = db.execute(
                    "INSERT OR IGNORE INTO deliveries (campaign, chat_id, username) VALUES (?, ?, ?)",
                    (*key, username),
                )
            if not claim.rowcount:
                (message_id,) = db.execute(
                    "SELECT message_id FROM deliveries WHERE campaign = ? AND chat_id = ?",
                    key,
                ).fetchone()
                if message_id is None:
                    print(
                        f"Пропуск {url} — проверьте в Telegram результат предыдущей попытки",
                        file=sys.stderr,
                    )
                    failures += 1
                else:
                    print(f"Уже опубликовано — {url}/{message_id}")
                continue

            try:
                sent = await client.send_message(
                    chat, message, parse_mode=None, link_preview=False
                )
            except (
                errors.FloodWaitError,
                errors.SlowModeWaitError,
                errors.PeerFloodError,
                errors.ChatWriteForbiddenError,
                errors.ChatAdminRequiredError,
                errors.UserBannedInChannelError,
            ) as exc:
                # Telegram отклонил отправку, поэтому запись можно снять.
                with db:
                    db.execute(
                        "DELETE FROM deliveries WHERE campaign = ? AND chat_id = ?", key
                    )
                print(
                    f"Остановка на {url} — Telegram отклонил отправку ({exc})",
                    file=sys.stderr,
                )
                return 1
            except Exception:
                # При обрыве соединения Telegram мог принять запрос до потери ответа.
                print(
                    f"Остановка на {url} — результат неизвестен, проверьте историю чата",
                    file=sys.stderr,
                )
                raise
            with db:
                db.execute(
                    "UPDATE deliveries SET message_id = ? WHERE campaign = ? AND chat_id = ?",
                    (sent.id, *key),
                )
            print(f"Опубликовано — {url}/{sent.id}", flush=True)
        return int(bool(failures))


async def publish(args, targets, message):
    from telethon import TelegramClient, errors

    api_id = int(os.environ["TELEGRAM_API_ID"])
    api_hash = os.environ["TELEGRAM_API_HASH"]
    session = os.environ.get("TELEGRAM_SESSION", str(ROOT / "gigkod"))
    client = TelegramClient(
        session,
        api_id,
        api_hash,
        flood_sleep_threshold=0,
        request_retries=0,
        raise_last_call_error=True,
    )
    try:
        await client.start()
        return await deliver(client, args, targets, message)
    except errors.RPCError as exc:
        print(f"Остановка — Telegram вернул ошибку ({exc})", file=sys.stderr)
        return 1
    finally:
        await client.disconnect()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--chats",
        type=Path,
        default=ROOT / "users.txt",
        help="Список согласованных групп",
    )
    parser.add_argument(
        "--template", type=Path, default=ROOT / "template.txt", help="Текст объявления"
    )
    parser.add_argument(
        "--journal", type=Path, default=ROOT / "sent.sqlite3", help="Журнал отправок"
    )
    parser.add_argument(
        "--campaign", default="gigkod-intro", help="Идентификатор кампании в журнале"
    )
    parser.add_argument(
        "--send", action="store_true", help="Опубликовать сообщение в указанных группах"
    )
    args = parser.parse_args()

    try:
        targets = load_targets(args.chats)
        message = args.template.read_text(encoding="utf-8-sig").strip()
        if not targets:
            raise ValueError(f"{args.chats.name}: в списке нет групп")
        if not message or len(message.encode("utf-16-le")) > 8192:
            raise ValueError(
                "Текст объявления должен содержать от 1 до 4096 единиц UTF-16"
            )
        print(f"Кампания {args.campaign}. Групп в списке — {len(targets)}")
        for target in targets:
            print(f"  https://t.me/{target}")
        print(f"\n{message}\n", flush=True)
        if not args.send:
            print("Предпросмотр. Для публикации укажите --send.")
            return 0
        return asyncio.run(publish(args, targets, message))
    except KeyError as exc:
        print(f"Укажите переменную окружения {exc.args[0]}.", file=sys.stderr)
    except ImportError:
        print(
            "Установите зависимости: python3 -m pip install -r requirements.txt",
            file=sys.stderr,
        )
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"Ошибка ({type(exc).__name__}): {exc}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
