import os
import sys
import json
import time
import shutil
import getpass
import threading
import logging
import re
from datetime import timedelta
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QTreeWidget, QTreeWidgetItem,
    QMessageBox, QSplitter, QProgressBar, QDialog, QListWidget, QListWidgetItem,
    QHeaderView, QCheckBox, QFrame, QInputDialog
)
from PyQt6.QtCore import Qt, QSize, QThread, pyqtSignal, QObject, QEvent
from PyQt6.QtGui import QIcon, QFont, QColor, QPalette

# === КОНФИГУРАЦИЯ & НАСТРОЙКИ ===
CACHE_FILE = os.path.join(os.path.dirname(__file__), "cleaner_cache.json")
DAYS_OLD = 60
CACHE_MAX_AGE = 7 * 86400  # 7 дней
SCAN_ROOT = 'C:\\' if sys.platform.startswith('win') else os.path.expanduser('~')

# Системные пути, которые всегда исключаются для безопасности.
# Минимальный список для сканирования C:\
SYSTEM_PATHS = [
    r'C:\Windows',
    r'C:\System Volume Information',
    r'C:\Program Files',
    r'C:\Program Files (x86)',
    r'C:\ProgramData',
    r'C:\$Recycle.Bin'
] if sys.platform.startswith('win') else [
    '/bin','/etc','/usr','/lib','/lib64','/boot','/dev','/proc','/sys','/var','/opt','/root','/sbin'
]

# Ключевые слова для быстрого поиска мусорных папок с присвоением категории
TEMP_KEYWORDS = {
    'cache': '[Кэш]',
    '.cache': '[Кэш]',
    'temp': '[Временные]',
    'tmp': '[Временные]',
    'log': '[Логи]',
    'logs': '[Логи]',
    'roaming': '[Roaming]',
    'local': '[Local]',
    'locallow': '[LocalLow]',
    'site-packages': '[Python Кэш]',
    '__pycache__': '[Python Кэш]',
    '.npm': '[NPM Кэш]',
    'vendor': '[Вендор]', # Общая категория для временных библиотек
    '.venv': '[Вирт. Среда]',
    'venv': '[Вирт. Среда]',
}

# Расширения для быстрого поиска мусорных файлов
TRASH_EXT = {
    '.log', '.tmp', '.temp', '.bak', '.old', '.cache', '.junk',
    '.dmp', '.err', '.dump', '.swp', '.obj', '.o', '.pyc', '.class'
}

# === СТИЛЬ & ЦВЕТОВАЯ СХЕМА (MODERN DARK MODE) ===
STYLE_SHEET = """
    QMainWindow { background-color: #1f2833; }
    QWidget { background-color: #1f2833; color: #f2f2f2; font-family: Inter; }
    QLabel#TitleLabel { color: #66fcf1; font-size: 24pt; font-weight: bold; }
    QLabel { font-size: 10pt; }

    QTreeWidget {
        background-color: #2c3846;
        color: #f2f2f2;
        border: 1px solid #4a5a6b;
        selection-background-color: #0b7c7c;
        selection-color: #ffffff;
        padding: 5px;
        font-size: 10pt;
        border-radius: 6px;
    }
    QHeaderView::section {
        background-color: #3e4a59;
        color: #66fcf1;
        padding: 8px;
        border: 1px solid #4a5a6b;
        font-weight: bold;
    }

    QPushButton {
        background-color: #45a29e;
        color: #ffffff;
        border-radius: 8px;
        padding: 10px 15px;
        font-weight: bold;
        font-size: 10pt;
        border: none;
    }
    QPushButton:hover {
        background-color: #5ab6b2;
    }
    QPushButton#StopButton { background-color: #c53c3c; }
    QPushButton#StopButton:hover { background-color: #e54b4b; }
    QPushButton#DeleteButton { background-color: #a31c1c; }
    QPushButton#DeleteButton:hover { background-color: #c92222; }
    QPushButton#PreviewButton { background-color: #1f78c1; }
    QPushButton#PreviewButton:hover { background-color: #2b8ce8; }

    QLineEdit, QComboBox {
        background-color: #344354;
        color: #f2f2f2;
        border: 1px solid #4a5a6b;
        padding: 6px;
        border-radius: 4px;
    }
    QProgressBar {
        border: 1px solid #4a5a6b;
        border-radius: 5px;
        text-align: center;
        background-color: #344354;
    }
    QProgressBar::chunk {
        background-color: #66fcf1;
    }
"""

# === УТИЛИТЫ ===

def human(size):
    """Преобразование байтов в читаемый формат (GB, MB, KB и т.д.)"""
    # Используем KiB/MiB/GiB для совместимости со скриншотами
    for unit in ['B', 'KiB', 'MiB', 'GiB', 'TiB']:
        if abs(size) < 1024:
            # Используем rjust для выравнивания, если необходимо, но для простоты убираем
            return f"{size:,.1f} {unit}".replace(',', ' ')
        size /= 1024
    return f"{size:,.1f} PiB".replace(',', ' ')

def size_to_bytes(human_size_str):
    """
    Преобразование читаемого формата (например, "1.2 GiB") обратно в байты для сортировки.
    Улучшено для надежного парсинга.
    """
    if not isinstance(human_size_str, str):
        return 0
    
    human_size_str = human_size_str.strip().replace(',', '.')
    
    # Регулярное выражение для поиска числа и единиц (например, 1.2, GiB)
    match = re.match(r"(\d+(\.\d+)?)\s*([KMGTPE]i?B)", human_size_str, re.IGNORECASE)
    
    if not match:
        # Проверка на просто "B" (например, "820.0 B")
        if human_size_str.endswith('B') and len(human_size_str.split()) == 2:
            try:
                size_str, _ = human_size_str.split()
                return int(float(size_str))
            except:
                return 0
        return 0
        
    size = float(match.group(1))
    unit_str = match.group(3).upper().replace('IB', '').replace('B', '') # MIB -> M, GB -> G

    units = {'K': 1024, 'M': 1024**2, 'G': 1024**3, 'T': 1024**4, 'P': 1024**5}
    
    multiplier = units.get(unit_str, 1)
    
    return int(size * multiplier)


def load_cache():
    """Загрузка данных из кэша"""
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)

        found_items = {}
        now = time.time()

        for path, info in data.items():
            last_scan = info.get('last_scan', 0)
            # Проверка актуальности кэша и существования файла
            if now - last_scan <= CACHE_MAX_AGE and os.path.exists(path):
                found_items[path] = info

        logging.info(f"Кэш загружен: {len(found_items)} элементов")
        return found_items
    except Exception as e:
        logging.error(f"Ошибка загрузки кэша: {e}")
        return {}

def save_cache(items):
    """Сохранение данных в кэш"""
    try:
        cache_data = {}
        current_time = time.time()
        for path, info in items.items():
            cache_data[path] = {
                'type': info['type'],
                'size': info['size'],
                'count': info.get('count', 1),
                'category': info.get('category', 'Неизвестно'),
                'last_scan': current_time
            }
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
        logging.info("Кэш сохранён")
    except Exception as e:
        logging.error(f"Ошибка сохранения кэша: {e}")

def is_system_or_skip(path):
    """Проверка пути на принадлежность к системным или исключенным"""
    try:
        abs_path = os.path.normcase(os.path.abspath(path))
    except:
        return True # Недоступный путь

    return any(abs_path.startswith(os.path.normcase(os.path.abspath(s))) for s in SYSTEM_PATHS)

# === ЛОГИКА СКАНИРОВАНИЯ (Рабочий поток) ===

class Scanner(QObject):
    """Рабочий объект для выполнения сканирования в отдельном потоке."""

    # Сигналы для связи с GUI
    progress_update = pyqtSignal(str)
    scan_complete = pyqtSignal(dict)

    def __init__(self, days_old):
        super().__init__()
        self.days_old = days_old
        self.stop_event = threading.Event()

    def stop(self):
        """Установка флага остановки."""
        self.stop_event.set()

    def run_scan(self):
        """Основной метод запуска сканирования."""
        self.stop_event.clear()
        all_found_items = {}

        # --- ФАЗА 1: Быстрое системное сканирование мусора (C:\) ---
        self.progress_update.emit("Фаза 1/2: Быстрое сканирование мусора (C:\\)...")
        trash_results = self.quick_trash_scan(SCAN_ROOT)
        all_found_items.update(trash_results)

        if self.stop_event.is_set():
            self.scan_complete.emit({})
            return

        # --- ФАЗА 2: Глубокое сканирование "старых" файлов (~Home) ---
        self.progress_update.emit("Фаза 2/2: Глубокое сканирование старых файлов (60+ дней)...")
        home_dir = os.path.expanduser('~')

        # Интеллектуальное группирование старых файлов
        old_proposals = self.intelligent_grouping_old_files(home_dir)

        # Обработка предложений старых файлов
        for p in old_proposals:
            if p not in all_found_items:
                try:
                    is_dir = os.path.isdir(p)
                    size = 0
                    count = 0

                    if is_dir:
                        # Пересчитываем размер и количество старых файлов внутри папки
                        for root_dir, _, files in os.walk(p):
                            for f in files:
                                fp = os.path.join(root_dir, f)
                                st = os.stat(fp)
                                size += st.st_size
                                if max(st.st_atime, st.st_mtime, st.st_ctime) < (time.time() - self.days_old * 86400):
                                    count += 1
                    else:
                        size = os.path.getsize(p)
                        count = 1

                    if size > 0:
                        all_found_items[p] = {
                            'type': 'dir' if is_dir else 'file',
                            'size': size,
                            'count': count,
                            'category': "Старый Файл (60+)",
                            'last_scan': time.time()
                        }
                except Exception:
                    pass

        self.progress_update.emit(f"Сканирование завершено. Найдено: {len(all_found_items)} уникальных элементов.")
        self.scan_complete.emit(all_found_items)

    # === ВНУТРЕННИЕ АЛГОРИТМЫ СКАНИРОВАНИЯ ===

    def quick_trash_scan(self, root_dir):
        """
        Быстрое сканирование по ключевым словам и расширениям.
        Разбивает большие папки AppData/Roaming на подпапки для лучшего контроля.
        """
        trash_items = {}

        for dirpath, dirnames, filenames in os.walk(root_dir, topdown=True):
            if self.stop_event.is_set():
                return trash_items

            # 1. Проверка на системную папку
            if is_system_or_skip(dirpath):
                dirnames[:] = []
                continue

            dir_name = os.path.basename(dirpath).lower()

            # --- Логика деления AppData/Roaming/Local ---
            is_appdata_root = any(name in dir_name for name in ['local', 'roaming', 'locallow'])

            # 2. Быстрая проверка на Папку-Мусор (по ключевым словам)
            found_keyword = next((kw for kw in TEMP_KEYWORDS if kw in dir_name), None)

            if found_keyword and dirpath != root_dir:
                category = TEMP_KEYWORDS[found_keyword]
                try:
                    # Группируем как одну папку для удаления
                    size, count = self._calculate_dir_size_and_count(dirpath)

                    if size > 1024 * 1024: # Ищем папки > 1MB
                        trash_items[dirpath] = {
                            'type': 'trash_dir',
                            'size': size,
                            'count': count,
                            'category': f"Мусор ({category})",
                            'last_scan': time.time()
                        }

                    # Если нашли мусор, дальше по этой ветке не идем
                    dirnames[:] = []
                    continue
                except Exception:
                    dirnames[:] = []
                    continue

            # Если это папка AppData/Local или Roaming, ищем мусор в её непосредственных подпапках
            if is_appdata_root and 'appdata' in os.path.normcase(dirpath):
                # Начинаем сканирование каждого подкаталога как отдельного элемента
                for dirname in list(dirnames):
                    subdirpath = os.path.join(dirpath, dirname)
                    if self.stop_event.is_set(): return trash_items

                    try:
                        # Ищем мусорные ключевые слова в имени подпапки
                        if any(kw in dirname.lower() for kw in TEMP_KEYWORDS):
                            continue # Пропустим, если она сама по себе является мусором, чтобы не дублировать

                        size, count = self._calculate_dir_size_and_count(subdirpath)

                        # Если подпапка большая, ищем в ней мусор по расширениям
                        if size > 10 * 1024 * 1024: # > 10MB
                            trash_files_in_subdir = 0
                            for _, _, fs in os.walk(subdirpath):
                                for f in fs:
                                    ext = os.path.splitext(f)[1].lower()
                                    if ext in TRASH_EXT:
                                        trash_files_in_subdir += 1

                            if trash_files_in_subdir > 0 and size > 10 * 1024 * 1024:
                                trash_items[subdirpath] = {
                                    'type': 'trash_dir',
                                    'size': size,
                                    'count': trash_files_in_subdir,
                                    'category': "Мусор (Кэш Приложений)",
                                    'last_scan': time.time()
                                }

                    except Exception:
                        pass

                # После сканирования подпапок, все равно продолжаем обход, чтобы поймать мусорные файлы

            # 3. Поиск Мусорных Файлов (по расширению)
            for fn in filenames:
                ext = os.path.splitext(fn)[1].lower()
                if ext in TRASH_EXT:
                    fp = os.path.join(dirpath, fn)
                    try:
                        size = os.path.getsize(fp)
                        if size > 0:
                            trash_items[fp] = {
                                'type': 'trash_file',
                                'size': size,
                                'count': 1,
                                'category': "Мусор (Файл/Лог)",
                                'last_scan': time.time()
                            }
                    except:
                        pass

        return trash_items

    def _calculate_dir_size_and_count(self, dirpath):
        """Быстрый подсчет размера и количества файлов в папке."""
        total_size = 0
        total_count = 0
        for root, _, files in os.walk(dirpath):
            for f in files:
                try:
                    fp = os.path.join(root, f)
                    total_size += os.path.getsize(fp)
                    total_count += 1
                except:
                    pass
        return total_size, total_count

    # --- АЛГОРИТМЫ СТАРЫХ ФАЙЛОВ (для Фазы 2) ---

    def intelligent_grouping_old_files(self, root_dir):
        """Интеллектуальный поиск и группировка старых файлов."""

        # 1. Построение дерева с информацией о старых файлах
        tree = self._build_old_tree(root_dir)
        if self.stop_event.is_set():
            return set()

        # 2. Интеллектуальный мерджинг папок
        proposals = set()
        if root_dir in tree:
            self._merge_recursive_old(tree[root_dir], root_dir, proposals, tree)
        return proposals

    def _build_old_tree(self, root_dir):
        """Строит дерево каталогов, считая 'старые' файлы."""
        tree = {}
        threshold = time.time() - self.days_old * 86400

        # Только для Home/Documents/Downloads
        scan_folders = [root_dir]
        if sys.platform.startswith('win'):
            # Добавляем Documents, Downloads, Pictures
            user = getpass.getuser()
            doc_paths = [
                os.path.join('C:\\Users', user, 'Documents'),
                os.path.join('C:\\Users', user, 'Downloads'),
                os.path.join('C:\\Users', user, 'Pictures'),
            ]
            for p in doc_paths:
                if os.path.exists(p) and p not in scan_folders:
                    scan_folders.append(p)

        for r_dir in scan_folders:
            for dirpath, dirnames, filenames in os.walk(r_dir, topdown=False):
                if self.stop_event.is_set(): return {}
                if is_system_or_skip(dirpath):
                    dirnames[:] = []
                    continue

                node = tree.setdefault(dirpath, {
                    'old_files': [], 'all_files': [], 'old_size': 0, 'real_size': 0,
                    'subdirs': {}, 'total_old_count': 0, 'total_real_size': 0
                })

                for fn in filenames:
                    fp = os.path.join(dirpath, fn)
                    try:
                        st = os.stat(fp)
                        node['all_files'].append(fp)
                        node['real_size'] += st.st_size
                        if max(st.st_atime, st.st_mtime, st.st_ctime) < threshold:
                            node['old_files'].append(fp)
                            node['old_size'] += st.st_size
                    except:
                        pass

                for subdir in list(dirnames):
                    subpath = os.path.join(dirpath, subdir)
                    if subpath in tree:
                        subnode = tree[subpath]
                        node['subdirs'][subdir] = subnode
                        node['total_old_count'] += subnode['total_old_count']
                        node['total_real_size'] += subnode['total_real_size']

                node['total_old_count'] += len(node['old_files'])
                node['total_real_size'] += node['real_size']

                dirnames[:] = [d for d in dirnames if not is_system_or_skip(os.path.join(dirpath, d))]

        return tree

    def _merge_recursive_old(self, node, path, proposals, tree=None):
        """Рекурсивно объединяет папки с высоким содержанием старых файлов."""
        if self.stop_event.is_set(): return 0, 0

        old_count = node['total_old_count']
        total_real_size = node['total_real_size']

        # 1. Сначала рекурсивно обрабатываем подкаталоги
        for subdir, subnode in node['subdirs'].items():
            subpath = os.path.join(path, subdir)
            self._merge_recursive_old(subnode, subpath, proposals, tree)

        # 2. Логика объединения для текущей папки

        # Процент старых файлов в текущей папке + подпапках
        total_files = len(node['all_files']) + sum(len(tree[os.path.join(path, d)]['all_files']) for d in node['subdirs'])
        old_ratio = old_count / total_files if total_files > 0 else 0

        # Правила: Если папка содержит 80% старых файлов ИЛИ это системная папка с 60%+
        is_temp_or_system = any(kw in path.lower() for kw in TEMP_KEYWORDS) or 'appdata' in os.path.normcase(path)

        merge_threshold = 0.85 if not is_temp_or_system else 0.6

        should_merge = (old_ratio >= merge_threshold and old_count > 5)

        if should_merge and total_real_size > 0:
            # Предлагаем папку целиком
            proposals.add(path)
        else:
            # Если папку не объединяем, предлагаем только отдельные старые файлы в ней
            for fp in node['old_files']:
                try:
                    if os.path.exists(fp) and os.path.getsize(fp) > 0:
                        proposals.add(fp)
                except:
                    pass

        return old_count, total_real_size

# === GUI (PyQt6) ===

class CleanerApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Smart File Cleaner (PyQt6)")
        self.setGeometry(100, 100, 1500, 900)
        self.setStyleSheet(STYLE_SHEET)

        self.found_items = {}
        self.scanner_thread = None
        self.scanner_worker = None

        self._setup_ui()
        self._load_data()

    def _setup_ui(self):
        """Настройка основного интерфейса."""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)

        # Заголовок
        title_label = QLabel("Smart File Cleaner")
        title_label.setObjectName("TitleLabel")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(title_label)

        # Панель управления (Кнопки + Прогресс)
        control_frame = QFrame()
        control_layout = QHBoxLayout(control_frame)
        control_layout.setSpacing(15)

        self.scan_btn = QPushButton("Начать сканирование")
        self.scan_btn.clicked.connect(self.start_scan)

        self.stop_btn = QPushButton("Стоп")
        self.stop_btn.setObjectName("StopButton")
        self.stop_btn.clicked.connect(self.stop_scan)
        self.stop_btn.setEnabled(False)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(25)
        self.progress_bar.setVisible(False)

        self.status_label = QLabel("Готов к сканированию")
        self.status_label.setFixedWidth(300)

        control_layout.addWidget(self.scan_btn)
        control_layout.addWidget(self.stop_btn)
        control_layout.addWidget(self.progress_bar)
        control_layout.addWidget(self.status_label)
        control_layout.setStretch(2, 1) # Прогресс-бар занимает больше места

        main_layout.addWidget(control_frame)

        # Фильтры и Поиск
        filter_frame = QFrame()
        filter_layout = QHBoxLayout(filter_frame)
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.setSpacing(10)

        # Поиск
        filter_layout.addWidget(QLabel("Поиск:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Фильтрация по имени или пути...")
        self.search_input.textChanged.connect(self.filter_tree)
        filter_layout.addWidget(self.search_input)

        # Расширения
        filter_layout.addWidget(QLabel("Расширения (через пробел):"))
        self.ext_input = QLineEdit()
        self.ext_input.setPlaceholderText(".zip .iso")
        self.ext_input.setFixedWidth(150)
        self.ext_input.textChanged.connect(self.filter_tree)
        filter_layout.addWidget(self.ext_input)

        # Чекбокс для мусорных расширений
        self.trash_ext_checkbox = QCheckBox("Вкл. мусорные (.log, .tmp, etc.)")
        self.trash_ext_checkbox.setChecked(True)
        self.trash_ext_checkbox.stateChanged.connect(self.filter_tree)
        filter_layout.addWidget(self.trash_ext_checkbox)

        main_layout.addWidget(filter_frame)

        # Таблица (TreeWidget)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(5)
        self.tree.setHeaderLabels(["Имя", "Путь", "Категория", "Размер", "Файлов"])
        self.tree.setSortingEnabled(False) # Отключаем стандартную сортировку

        # *** ИСПОЛЬЗУЕМ РУЧНУЮ СОРТИРОВКУ ***
        self.tree.header().sectionClicked.connect(self.on_header_clicked)
        self.current_sort_column = 3 # Сортировка по размеру по умолчанию
        self.current_sort_order = Qt.SortOrder.DescendingOrder

        self.tree.header().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.tree.header().setStretchLastSection(False)

        # Установка ширины колонок
        self.tree.columnWidths = [300, 450, 180, 120, 80]
        for i, width in enumerate(self.tree.columnWidths):
            self.tree.setColumnWidth(i, width)

        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.itemSelectionChanged.connect(self.update_selection_count)
        self.tree.itemDoubleClicked.connect(self.toggle_item_check)

        main_layout.addWidget(self.tree)

        # Нижняя панель с кнопками действий и статистикой
        action_frame = QFrame()
        action_layout = QHBoxLayout(action_frame)
        action_layout.setContentsMargins(0, 0, 0, 0)

        self.select_all_btn = QPushButton("Выделить всё")
        self.select_all_btn.clicked.connect(lambda: self._set_selection_state(True))

        self.unselect_all_btn = QPushButton("Снять всё")
        self.unselect_all_btn.clicked.connect(lambda: self._set_selection_state(False))

        self.preview_btn = QPushButton("Предпросмотр")
        self.preview_btn.setObjectName("PreviewButton")
        self.preview_btn.clicked.connect(self.show_preview_dialog)

        self.delete_btn = QPushButton("Удалить выбранное")
        self.delete_btn.setObjectName("DeleteButton")
        self.delete_btn.clicked.connect(self.delete_selected_items)
        self.delete_btn.setEnabled(False) # Изначально отключена

        self.selection_status_label = QLabel("Выбрано: 0 | Общий размер: 0 B")
        self.selection_status_label.setFont(QFont("Inter", 10, QFont.Weight.Bold))
        self.selection_status_label.setMinimumWidth(300)

        action_layout.addWidget(self.select_all_btn)
        action_layout.addWidget(self.unselect_all_btn)
        action_layout.addSpacing(30)
        action_layout.addWidget(self.preview_btn)
        action_layout.addWidget(self.delete_btn)
        action_layout.addStretch(1)
        action_layout.addWidget(self.selection_status_label)

        main_layout.addWidget(action_frame)

    def _load_data(self):
        """Загрузка кэша и отображение данных."""
        self.found_items = load_cache()
        if self.found_items:
            self.status_label.setText(f"Загружено {len(self.found_items)} из кэша. Нажмите 'Сканировать' для обновления.")
            self.filter_tree()
            # При загрузке кэша сразу сортируем по размеру
            self.on_header_clicked(self.current_sort_column)
        else:
            # Автоматический запуск при первом запуске
            self.start_scan()

    # === МЕТОДЫ ДЛЯ TreeWidget ===

    def on_header_clicked(self, index):
        """Обработка клика по заголовку для ручной сортировки."""

        # 1. Определяем порядок сортировки
        if self.current_sort_column == index:
            self.current_sort_order = Qt.SortOrder.DescendingOrder if self.current_sort_order == Qt.SortOrder.AscendingOrder else Qt.SortOrder.AscendingOrder
        else:
            self.current_sort_column = index
            self.current_sort_order = Qt.SortOrder.AscendingOrder

        self.tree.header().setSortIndicator(index, self.current_sort_order)
        is_desc = self.current_sort_order == Qt.SortOrder.DescendingOrder

        # 2. Собираем все корневые элементы
        items = []
        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            items.append(root.child(i))

        # 3. Отсоединяем элементы для быстрой сортировки
        for item in items:
            root.takeChild(root.indexOfChild(item))

        # 4. Логика сортировки
        if index == 3: # Колонка "Размер"
            # Кастомная сортировка по байтам
            def sort_key(item):
                size_str = item.text(3)
                return size_to_bytes(size_str)

            items.sort(key=sort_key, reverse=is_desc)

        elif index == 4: # Колонка "Файлов"
             # Сортировка по числу (пустые строки = 0)
            def sort_key(item):
                try:
                    return int(item.text(4) or 0)
                except ValueError:
                    return 0

            items.sort(key=sort_key, reverse=is_desc)

        else: # Остальные колонки (Имя, Путь, Категория)
            # Стандартная текстовая сортировка
            items.sort(key=lambda item: item.text(index), reverse=is_desc)

        # 5. Возвращаем отсортированные элементы обратно в QTreeWidget
        # *** ИСПРАВЛЕНИЕ: addTopLevelItems вызывается на QTreeWidget, а не QTreeWidgetItem ***
        self.tree.addTopLevelItems(items)


    def filter_tree(self):
        """Фильтрация данных в таблице по поиску и расширениям."""
        self.tree.clear()
        query = self.search_input.text().lower().strip()

        # Фильтры расширений
        ext_filter_str = self.ext_input.text().lower().strip()
        custom_ext_filter = {e.strip() for e in ext_filter_str.split() if e.startswith('.')} if ext_filter_str else None

        if self.trash_ext_checkbox.isChecked():
            # Объединяем пользовательские фильтры с системным мусором
            custom_ext_filter = (custom_ext_filter or set()) | TRASH_EXT

        items_to_add = []

        for orig_path, info in self.found_items.items():
            name = os.path.basename(orig_path) or orig_path

            # 1. Фильтр по поисковому запросу
            if query and query not in name.lower() and query not in orig_path.lower():
                continue

            # 2. Фильтр по расширениям (только для файлов)
            if info['type'] == 'file' or info['type'] == 'trash_file':
                ext = os.path.splitext(orig_path)[1].lower()
                if custom_ext_filter and ext not in custom_ext_filter:
                    continue

            # Форматирование
            sz = human(info['size'])
            cnt = str(info['count']) if info.get('count', 0) > 0 else ''

            # Название элемента: делаем его более информативным
            display_name = name
            if 'Мусор' in info['category']:
                # Для мусора используем более читаемое название папки
                kw_match = re.search(r'\((.+?)\)', info['category'])
                kw = kw_match.group(1) if kw_match else ''
                display_name = f"{name} {kw}"

            item = QTreeWidgetItem([display_name, orig_path, info['category'], sz, cnt])

            # Установка флага Checkable (выделение)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable)
            item.setCheckState(0, Qt.CheckState.Unchecked)

            # Добавляем реальный путь в Data для быстрого доступа
            item.setData(0, Qt.ItemDataRole.UserRole, orig_path)

            items_to_add.append(item)

            # Если это папка, добавляем дочерний элемент-пример
            if info['type'] in ('dir', 'trash_dir') and info.get('count', 0) > 0:
                sample_path = None
                try:
                    # Попытка найти первый файл в папке
                    for root_dir, _, files in os.walk(orig_path):
                        if files:
                            sample_path = os.path.join(root_dir, files[0])
                            break
                        if sample_path: break
                except:
                    pass

                if sample_path:
                    ch_item = QTreeWidgetItem(["... (Пример содержимого)", sample_path, 'Внутри папки', '?', ''])
                    # Дочерний элемент не должен быть чекбоксом
                    ch_item.setFlags(Qt.ItemFlag.ItemIsSelectable)
                    item.addChild(ch_item)

        self.tree.addTopLevelItems(items_to_add)

        # После фильтрации применяем текущую сортировку
        if self.current_sort_column != -1:
            self.on_header_clicked(self.current_sort_column)

        self.update_selection_count()

    def update_selection_count(self):
        """Обновляет статистику по выбранным элементам."""
        total_selected = 0
        total_size = 0

        # Собираем уникальные пути, которые действительно будут удалены (только верхний уровень)
        paths_to_delete = set()

        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            item = root.child(i)
            # Проверяем чекбокс
            if item.checkState(0) == Qt.CheckState.Checked:
                path = item.data(0, Qt.ItemDataRole.UserRole)
                if path in self.found_items:
                    paths_to_delete.add(path)
                    total_selected += 1
                    total_size += self.found_items[path]['size']

        self.selection_status_label.setText(f"Выбрано: {total_selected} | Общий размер: {human(total_size)}")
        self.delete_btn.setEnabled(total_selected > 0)

    def toggle_item_check(self, item, column):
        """Обрабатывает двойной клик или нажатие на чекбокс."""
        if item.parent() is None: # Только для корневых элементов
            # Переключаем чекбокс при клике на любую колонку
            new_state = Qt.CheckState.Unchecked if item.checkState(0) == Qt.CheckState.Checked else Qt.CheckState.Checked
            item.setCheckState(0, new_state)
            self.update_selection_count()

    def _set_selection_state(self, checked):
        """Выделяет/снимает выделение со всех корневых элементов."""
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            item = root.child(i)
            item.setCheckState(0, state)
        self.update_selection_count()

    # === МЕТОДЫ УПРАВЛЕНИЯ СКАНИРОВАНИЕМ ===

    def start_scan(self):
        """Запуск сканирования в отдельном потоке."""
        if self.scanner_thread and self.scanner_thread.isRunning():
            return

        self.found_items.clear()
        self.tree.clear()
        # Сбрасываем сортировку
        self.tree.header().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        self.current_sort_column = -1

        self.scanner_thread = QThread()
        self.scanner_worker = Scanner(DAYS_OLD)
        self.scanner_worker.moveToThread(self.scanner_thread)

        self.scanner_thread.started.connect(self.scanner_worker.run_scan)
        self.scanner_worker.scan_complete.connect(self.on_scan_complete)
        self.scanner_worker.progress_update.connect(self.status_label.setText)
        self.scanner_thread.finished.connect(self.scanner_thread.deleteLater)
        self.scanner_worker.destroyed.connect(self.scanner_thread.quit)

        self.scan_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0) # Индикатор неопределенного прогресса

        self.scanner_thread.start()

    def stop_scan(self):
        """Остановка сканирования."""
        if self.scanner_worker:
            self.scanner_worker.stop()
        self.status_label.setText("Остановка...")

    def on_scan_complete(self, results):
        """Обработка результатов сканирования."""
        if self.scanner_thread:
            self.scanner_thread.quit()
            
        self.scan_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.progress_bar.setVisible(False)

        self.found_items = results
        save_cache(self.found_items)
        self.filter_tree()

        if not self.found_items:
             self.status_label.setText("Сканирование завершено. Ничего не найдено.")
        else:
             self.status_label.setText(f"Сканирование завершено. Найдено {len(self.found_items)}.")
             # Сортировка по размеру после завершения сканирования
             self.on_header_clicked(3) # Колонка 3 - Размер


    # === МЕТОДЫ ДЕЙСТВИЙ (Удаление/Предпросмотр) ===

    def show_preview_dialog(self):
        """Показывает диалоговое окно с элементами, которые будут удалены."""
        paths_to_delete, total_size = self._get_selected_paths()

        if not paths_to_delete:
            QMessageBox.information(self, "Предпросмотр", "Сначала выберите элементы для удаления.")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Предпросмотр удаления")
        dialog.setGeometry(200, 200, 800, 600)
        dialog.setStyleSheet(STYLE_SHEET)

        layout = QVBoxLayout(dialog)

        label = QLabel("Следующие элементы (папки/файлы) будут удалены НАВСЕГДА:")
        label.setFont(QFont("Inter", 10, QFont.Weight.Bold))
        label.setStyleSheet("color: #66fcf1;")
        layout.addWidget(label)

        # Объяснение по удалению
        explanation = QLabel(
            "Внимание: При выборе элемента (папки) удаляется <b>именно эта папка</b> со всем ее содержимым. "
            "Например, при удалении C:\\...\\AppData\\Local\\Temp будет удалена папка Temp и все внутри."
        )
        explanation.setWordWrap(True)
        explanation.setStyleSheet("color: #f2f2f2; background-color: #4a5a6b; padding: 10px; border-radius: 6px;")
        layout.addWidget(explanation)

        list_widget = QListWidget()
        list_widget.setStyleSheet("QListWidget { background-color: #2c3846; border: 1px solid #4a5a6b; } QListWidget::item { padding: 5px; }")

        for path in paths_to_delete:
            info = self.found_items.get(path, {})
            size = info.get('size', 0)
            count = info.get('count', 1)
            item_type = 'Папка' if info.get('type', '').endswith('dir') else 'Файл'

            display = f"[{human(size):<10}] [{item_type}] {path}"
            if count > 1 and item_type == 'Папка':
                display += f" ({count} файлов внутри)"

            list_item = QListWidgetItem(display)
            list_widget.addItem(list_item)

        list_widget.addItem(QListWidgetItem(""))
        total_item = QListWidgetItem(f"ИТОГО: {human(total_size)} ({len(paths_to_delete)} элементов)")
        total_item.setForeground(QColor("#66fcf1"))
        total_item.setFont(QFont("Inter", 11, QFont.Weight.Bold))
        list_widget.addItem(total_item)

        layout.addWidget(list_widget)

        # Кнопки
        button_frame = QFrame()
        button_layout = QHBoxLayout(button_frame)

        delete_btn = QPushButton("Удалить НАВСЕГДА")
        delete_btn.setObjectName("DeleteButton")
        delete_btn.clicked.connect(lambda: [dialog.accept(), self.delete_selected_items(confirm=False)]) # Пропускаем подтверждение

        cancel_btn = QPushButton("Отмена")
        cancel_btn.clicked.connect(dialog.reject)

        button_layout.addWidget(delete_btn)
        button_layout.addWidget(cancel_btn)
        layout.addWidget(button_frame)

        dialog.exec()

    def _get_selected_paths(self):
        """Возвращает список уникальных путей и общий размер выбранных элементов."""
        paths_to_delete = []
        total_size = 0

        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            item = root.child(i)
            if item.checkState(0) == Qt.CheckState.Checked:
                path = item.data(0, Qt.ItemDataRole.UserRole)
                if path in self.found_items:
                    paths_to_delete.append(path)
                    total_size += self.found_items[path]['size']

        return paths_to_delete, total_size

    def delete_selected_items(self, confirm=True):
        """Удаляет выбранные элементы с диска."""
        paths_to_delete, total_size = self._get_selected_paths()

        if not paths_to_delete:
            QMessageBox.information(self, "Удаление", "Сначала выберите элементы.")
            return

        if confirm:
            reply = QMessageBox.question(self, 'Подтверждение удаления',
                f"Вы уверены, что хотите навсегда удалить {len(paths_to_delete)} элементов общим размером {human(total_size)}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)

            if reply == QMessageBox.StandardButton.No:
                return
        
        # Если мы здесь, либо confirm=False (из предпросмотра), либо пользователь нажал Yes
        deleted_count = 0
        failed_paths = []

        # Используем отдельный поток для удаления, чтобы UI не зависал
        def deletion_worker():
            nonlocal deleted_count
            for path in paths_to_delete:
                try:
                    if os.path.isdir(path):
                        shutil.rmtree(path)
                    elif os.path.isfile(path):
                        os.remove(path)
                    else:
                        # Путь мог быть удален в прошлой итерации (например, подпапка)
                        if path in self.found_items:
                            self.found_items.pop(path, None)
                        continue

                    # Удаляем из found_items
                    self.found_items.pop(path, None)
                    deleted_count += 1
                except Exception as e:
                    logging.error(f"Ошибка удаления {path}: {e}")
                    failed_paths.append(path)

            # Обновляем UI после завершения
            QApplication.instance().postEvent(self, DeleteCompleteEvent(deleted_count, failed_paths))

        threading.Thread(target=deletion_worker, daemon=True).start()

        self.status_label.setText("Удаление...")
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.delete_btn.setEnabled(False)
        self.scan_btn.setEnabled(False)
        self.preview_btn.setEnabled(False)

    def customEvent(self, event):
        """Обрабатывает кастомное событие после завершения удаления."""
        if event.type() == DeleteCompleteEvent.EVENT_TYPE:
            self.progress_bar.setVisible(False)
            self.scan_btn.setEnabled(True)
            self.delete_btn.setEnabled(True)
            self.preview_btn.setEnabled(True)
            
            if event.failed_paths:
                self.status_label.setText(f"Удалено {event.deleted_count}. Ошибок: {len(event.failed_paths)}")
                QMessageBox.warning(self, "Ошибка удаления",
                    f"Не удалось удалить {len(event.failed_paths)} элементов (возможно, они заняты другим процессом):\n\n" +
                    "\n".join(event.failed_paths[:10]) + ("\n..." if len(event.failed_paths) > 10 else "")
                )
            else:
                self.status_label.setText(f"Удалено {event.deleted_count} элементов.")
            
            self.filter_tree() # Обновляем таблицу

class DeleteCompleteEvent(QEvent):
    """Кастомное событие для уведомления UI о завершении удаления."""
    EVENT_TYPE = QEvent.Type(QEvent.Type.User + 1)

    def __init__(self, count, failed_paths):
        super().__init__(self.EVENT_TYPE)
        self.deleted_count = count
        self.failed_paths = failed_paths

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # Настраиваем палитру для лучшего Dark Mode
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#1f2833"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#f2f2f2"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#344354"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#f2f2f2"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#0b7c7c"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)

    window = CleanerApp()
    window.show()
    sys.exit(app.exec())