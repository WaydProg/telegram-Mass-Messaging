from telethon.sync import TelegramClient
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.errors import FloodWaitError, UserPrivacyRestrictedError, InviteHashExpiredError, InviteHashInvalidError
import time
import asyncio
from datetime import datetime

import os

api_id = int(os.environ["TELEGRAM_API_ID"])
api_hash = os.environ["TELEGRAM_API_HASH"]

# Конфигурация
ARCHIVE_CHAT = "me"  # Куда отправлять информацию о вступивших чатах ("me" для сохраненных сообщений)
SEND_DELAY = 1  # Задержка между отправками сообщений
JOIN_DELAY = 5   # Задержка между вступлениями в чаты
CYCLE_DELAY = 60  # Задержка между циклами, если файл пуст

client = TelegramClient('6686606151', api_id, api_hash)

def parse_invite_link(link):
    """Парсит ссылку и возвращает username или хэш приглашения"""
    # Для публичных чатов (https://t.me/username)
    if link.startswith('https://t.me/'):
        parts = link.split('/')
        if len(parts) >= 4 and parts[3]:
            return parts[3]  # Возвращаем username без @
    
    # Для приватных чатов (https://t.me/+invitehash)
    if link.startswith('https://t.me/+'):
        return link.split('+')[1]  # Возвращаем хэш приглашения
    
    return None

async def join_chat(link):
    """Функция для вступления в чат/канал по ссылке"""
    try:
        identifier = parse_invite_link(link)
        if not identifier:
            print(f"❌ Неверный формат ссылки: {link}")
            return None

        # Для публичных чатов
        if not identifier.startswith('+'):
            await client(JoinChannelRequest(identifier))
            print(f"✅ Вступили в публичный чат: {identifier}")
            return identifier
        
        # Для приватных чатов с хэшем приглашения
        else:
            await client(ImportChatInviteRequest(identifier))
            print(f"✅ Вступили в приватный чат по хэшу")
            return identifier
            
    except FloodWaitError as e:
        print(f"⏱ FloodWait на {e.seconds} секунд при вступлении, спим...")
        await asyncio.sleep(e.seconds)
        return None
    except (InviteHashExpiredError, InviteHashInvalidError):
        print(f"❌ Недействительная или просроченная ссылка: {link}")
        return None
    except Exception as e:
        if "already" in str(e).lower():
            print(f"⚠️ Уже состоим в чате: {link}")
            return identifier if identifier else link
        print(f"❌ Ошибка при вступлении в {link}: {e}")
        return None

async def send_to_archive(link, entity):
    """Отправляет информацию о чате в архив"""
    try:
        # Получаем информацию о чате
        if hasattr(entity, 'title'):
            chat_title = entity.title
        elif hasattr(entity, 'username'):
            chat_title = f"@{entity.username}"
        elif hasattr(entity, 'first_name'):
            chat_title = f"{entity.first_name} {entity.last_name or ''}".strip()
        else:
            chat_title = "Неизвестный чат"
        
        # Создаем сообщение для архива
        archive_message = f"""
📋 ЗАПИСЬ В АРХИВ
────────────────
🕐 Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
🏷 Название: {chat_title}
🔗 Ссылка: {link}
👥 ID: {entity.id}
────────────────
"""
        
        # Отправляем в архив
        archive_entity = await client.get_entity(ARCHIVE_CHAT)
        await client.send_message(archive_entity, archive_message)
        print(f"📁 Информация о чате отправлена в архив: {chat_title}")
        
    except Exception as e:
        print(f"⚠️ Не удалось отправить в архив: {e}")

async def main_cycle():
    """Основной цикл рассылки"""
    await client.start()
    
    print("🤖 Бот запущен. Начинаю рассылку...")
    
    while True:  # Бесконечный цикл
        # Читаем шаблон сообщения
        try:
            with open("template.txt", "r", encoding="utf-8") as f:
                template = f.read().strip()
            
            if not template:
                print("⚠️ Файл template.txt пуст. Ожидаю 30 секунд...")
                await asyncio.sleep(30)
                continue
                
        except FileNotFoundError:
            print("❌ Файл template.txt не найден. Создайте файл с текстом для рассылки.")
            await asyncio.sleep(30)
            continue
        
        # Читаем список чатов
        try:
            with open("users.txt", "r", encoding="utf-8") as f:
                links = [line.strip() for line in f if line.strip()]
            
            if not links:
                print(f"⚠️ Файл users.txt пуст. Ожидаю {CYCLE_DELAY} секунд...")
                await asyncio.sleep(CYCLE_DELAY)
                continue
                
        except FileNotFoundError:
            print("❌ Файл users.txt не найден. Создайте файл со ссылками на чаты.")
            await asyncio.sleep(30)
            continue
        
        print(f"🔁 Начинаем новый цикл рассылки ({len(links)} чатов)")
        print(f"📝 Текст для рассылки: {template[:100]}..." if len(template) > 100 else f"📝 Текст для рассылки: {template}")
        
        success_count = 0
        fail_count = 0
        
        for i, link in enumerate(links):
            try:
                print(f"\n🔗 Обрабатываю чат {i+1}/{len(links)}: {link}")
                
                # Пытаемся вступить в чат
                identifier = await join_chat(link)
                if not identifier:
                    fail_count += 1
                    continue
                
                await asyncio.sleep(JOIN_DELAY)
                
                # Получаем entity чата для отправки сообщения
                try:
                    if identifier.startswith('+'):
                        # Для приватных чатов ищем через диалоги
                        entity_found = False
                        async for dialog in client.iter_dialogs(limit=100):
                            if dialog.is_channel or dialog.is_group:
                                # Проверяем разные варианты
                                if hasattr(dialog.entity, 'username') and dialog.entity.username:
                                    if f"t.me/{dialog.entity.username}" in link:
                                        entity = dialog.entity
                                        entity_found = True
                                        break
                                elif hasattr(dialog.entity, 'title'):
                                    # Можно добавить дополнительную логику для поиска по названию
                                    pass
                        
                        if not entity_found:
                            # Если не нашли, пробуем получить последний диалог (скорее всего это только что вступивший чат)
                            dialogs = await client.get_dialogs(limit=10)
                            if dialogs:
                                entity = dialogs[0].entity
                            else:
                                print(f"⚠️ Не удалось найти приватный чат: {link}")
                                fail_count += 1
                                continue
                    else:
                        entity = await client.get_entity(identifier)
                        
                except Exception as e:
                    print(f"❌ Не удалось получить информацию о чате {link}: {e}")
                    fail_count += 1
                    continue
                
                # Отправляем сообщение по шаблону
                try:
                    # Заменяем {username} в шаблоне если есть
                    if "{username}" in template:
                        if identifier.startswith('+'):
                            message = template.replace("{username}", "участник")
                        else:
                            message = template.replace("{username}", identifier)
                    else:
                        message = template
                    
                    await client.send_message(entity, message)
                    print(f"✅ Сообщение отправлено в: {link}")
                    success_count += 1
                    
                    # Отправляем информацию о чате в архив
                    await send_to_archive(link, entity)
                    
                except UserPrivacyRestrictedError:
                    print(f"🔒 Не удалось отправить сообщение (приватность ограничена): {link}")
                    fail_count += 1
                except FloodWaitError as e:
                    print(f"⏱ FloodWait на {e.seconds} секунд, спим...")
                    await asyncio.sleep(e.seconds)
                    # Пробуем снова после ожидания
                    try:
                        await client.send_message(entity, message)
                        print(f"✅ Сообщение отправлено в: {link}")
                        success_count += 1
                        await send_to_archive(link, entity)
                    except:
                        fail_count += 1
                except Exception as e:
                    print(f"❌ Ошибка отправки в {link}: {e}")
                    fail_count += 1
                
                # Задержка перед следующей отправкой
                if i < len(links) - 1:  # Не ждем после последней отправки
                    print(f"⏱ Ожидаю {SEND_DELAY} секунд до следующей отправки...")
                    await asyncio.sleep(SEND_DELAY)
                
            except Exception as e:
                print(f"❌ Критическая ошибка с {link}: {e}")
                fail_count += 1
        
        # Статистика цикла
        print(f"\n{'='*50}")
        print(f"📊 ЦИКЛ ЗАВЕРШЕН")
        print(f"✅ Успешно: {success_count}")
        print(f"❌ Неудачно: {fail_count}")
        print(f"🔁 Всего чатов: {len(links)}")
        print(f"{'='*50}")
        
        # Задержка перед следующим циклом
        print(f"\n🔄 Начинаю новый цикл через 10 секунд...")
        await asyncio.sleep(10)

async def main():
    """Главная функция"""
    try:
        await main_cycle()
    except KeyboardInterrupt:
        print("\n⚠️ Работа прервана пользователем")
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
    finally:
        await client.disconnect()

if __name__ == "__main__":
    print("""
╔══════════════════════════════════════════╗
║          ТЕЛЕГРАМ БОТ ДЛЯ РАССЫЛКИ       ║
║      И АРХИВАЦИИ ЧАТОВ                   ║
╚══════════════════════════════════════════╝
    
Функции:
1. Рассылает сообщение из template.txt по чатам из users.txt
2. Отправляет информацию о вступивших чатах в архив
3. Работает в зацикленном режиме
    
Требуемые файлы:
- template.txt - текст для рассылки
- users.txt - список ссылок на чаты
    
Для остановки нажмите Ctrl+C
    """)
    
    asyncio.run(main())