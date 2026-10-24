"""Современный интерфейс GTK4/libadwaita для JBL PartyBox 710."""
from __future__ import annotations

import json
import queue
import threading

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .protocol import PATTERNS, ZONES, PartyBox


class App:
    """Совместимая оболочка: App(address).mainloop() как в старом запускателе."""

    def __init__(self, address=''):
        self.address = address
        Gtk.Window.set_default_icon_name('org.partybox.Linux710')
        self.application = Adw.Application(
            application_id='org.partybox.Linux710',
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self.application.connect('activate', self._activate)
        self.window = None

    def mainloop(self):
        return self.application.run([])

    def _activate(self, _application):
        if self.window is not None:
            self.window.present()
            return
        self.window = PartyBoxWindow(self.application, self.address)
        self.window.present()


class PartyBoxWindow(Adw.ApplicationWindow):
    def __init__(self, application, address):
        super().__init__(application=application, title='JBL PartyBox 710')
        self.set_default_size(760, 760)
        self.set_size_request(640, 600)
        self.tasks = queue.Queue()
        self.results = queue.Queue()
        self.stopping = threading.Event()
        self.connected = False
        self.busy = False
        self.state = {}
        self.address = address
        self.audio_available = False
        self._updating_controls = False
        self._build()
        self.worker = threading.Thread(target=self._work, name='PartyBox-I/O', daemon=True)
        self.worker.start()
        self.connect('close-request', self._close)
        if address:
            self._submit_startup(address)

    def _build(self):
        header = Adw.HeaderBar()
        header.set_title_widget(Gtk.Label(label='PartyBox 710', css_classes=['title']))
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        self.set_content(toolbar)
        refresh = Gtk.Button.new_from_icon_name('view-refresh-symbolic')
        refresh.set_tooltip_text('Обновить состояние')
        refresh.set_accessible_role(Gtk.AccessibleRole.BUTTON)
        refresh.connect('clicked', lambda *_: self._submit('refresh'))
        header.pack_end(refresh)
        self.refresh_button = refresh

        scroll = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        page = Adw.PreferencesPage()
        scroll.set_child(page)
        toolbar.set_content(scroll)

        overview = Adw.PreferencesGroup(title='JBL PartyBox 710', description='Управление по Bluetooth LE')
        page.add(overview)
        row = Adw.ActionRow(title='Состояние BLE', subtitle='Не подключено')
        self.ble_row = row
        row.add_prefix(Gtk.Image.new_from_icon_name('bluetooth-symbolic'))
        self.connect_button = Gtk.Button(label='Подключиться', valign=Gtk.Align.CENTER)
        self.connect_button.add_css_class('suggested-action')
        self.connect_button.connect('clicked', self._on_connect)
        row.add_suffix(self.connect_button)
        overview.add(row)
        address_row = Adw.EntryRow(title='Bluetooth-адрес')
        address_row.set_text(self.address)
        address_row.connect('changed', self._on_address_changed)
        self.address_entry = address_row
        overview.add(address_row)

        audio = Adw.PreferencesGroup(title='Звук Linux', description='Маршрутизация выполняется только по отдельному запросу')
        page.add(audio)
        audio_row = Adw.ActionRow(title='Вывод звука', subtitle='Статус ещё не проверен')
        audio_row.add_prefix(Gtk.Image.new_from_icon_name('audio-speakers-symbolic'))
        self.audio_row = audio_row
        self.audio_button = Gtk.Button(label='Выводить звук через колонку', valign=Gtk.Align.CENTER)
        self.audio_button.connect('clicked', lambda *_: self._submit('route_audio'))
        audio_row.add_suffix(self.audio_button)
        audio.add(audio_row)
        audio_refresh = Gtk.Button(label='Проверить вывод звука', halign=Gtk.Align.END)
        audio_refresh.set_margin_top(12)
        audio_refresh.connect('clicked', lambda *_: self._submit('audio_status'))
        audio.add(audio_refresh)
        self.audio_refresh_button = audio_refresh

        self.firmware_row = Adw.ActionRow(title='Прошивка и MAC', subtitle='Появятся после чтения колонки')

        lighting = Adw.PreferencesGroup(title='Подсветка')
        page.add(lighting)
        pattern_row = Adw.ComboRow(title='Световое шоу')
        pattern_row.set_model(Gtk.StringList.new(list(PATTERNS.values())))
        self.pattern_row = pattern_row
        lighting.add(pattern_row)
        self.pattern_apply = Gtk.Button(label='Применить режим', halign=Gtk.Align.END)
        self.pattern_apply.connect('clicked', self._apply_pattern)
        self.pattern_apply.set_label('Применить')
        self.pattern_apply.set_valign(Gtk.Align.CENTER)
        pattern_row.add_suffix(self.pattern_apply)

        color_row = Adw.ActionRow(title='Цвет', subtitle='Не изменяется до нажатия кнопки')
        color_row.add_prefix(Gtk.Image.new_from_icon_name('color-select-symbolic'))
        initial = Gdk.RGBA(); initial.parse('#ff0000')
        self.color_button = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(), rgba=initial, valign=Gtk.Align.CENTER)
        color_row.add_suffix(self.color_button)
        lighting.add(color_row)
        self.color_apply = Gtk.Button(label='Применить цвет', halign=Gtk.Align.END)
        self.color_apply.connect('clicked', self._apply_color)
        self.color_apply.set_label('Применить')
        self.color_apply.set_valign(Gtk.Align.CENTER)
        color_row.add_suffix(self.color_apply)
        self.auto_color = Gtk.Button(label='Включить автоцвет', halign=Gtk.Align.END)
        self.auto_color.connect('clicked', lambda *_: self._submit('set_auto_color'))
        auto_row = Adw.ActionRow(title='Автосмена цветов')
        self.auto_color.set_label('Включить')
        self.auto_color.set_valign(Gtk.Align.CENTER)
        auto_row.add_suffix(self.auto_color)
        lighting.add(auto_row)
        self.zone_rows = {}
        for tag, label in ZONES.items():
            zone_row = Adw.ActionRow(title=label, subtitle='Нет данных')
            toggle = Gtk.Switch(valign=Gtk.Align.CENTER, sensitive=False)
            toggle.connect('state-set', self._zone_toggle, tag)
            zone_row.add_suffix(toggle)
            lighting.add(zone_row)
            self.zone_rows[tag] = (zone_row, toggle)

        sound = Adw.PreferencesGroup(title='Громкость и бас')
        page.add(sound)
        volume_row = Adw.ActionRow(title='Громкость колонки', subtitle='0–32 · ползунок не отправляет команду')
        self.volume_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 32, 1)
        self.volume_scale.set_draw_value(True)
        self.volume_scale.set_hexpand(True)
        self.volume_scale.set_size_request(140, -1)
        volume_row.set_subtitle_lines(2)
        volume_row.add_suffix(self.volume_scale)
        sound.add(volume_row)
        self.volume_apply = Gtk.Button(label='Применить громкость', halign=Gtk.Align.END)
        self.volume_apply.connect('clicked', self._apply_volume)
        self.volume_apply.set_label('Применить')
        self.volume_apply.set_valign(Gtk.Align.CENTER)
        volume_row.add_suffix(self.volume_apply)
        bass = Adw.ComboRow(title='Bass Boost')
        bass.set_model(Gtk.StringList.new(['Выключен', 'Уровень 1', 'Уровень 2']))
        self.bass_row = bass
        sound.add(bass)
        self.bass_apply = Gtk.Button(label='Применить Bass Boost', halign=Gtk.Align.END)
        self.bass_apply.connect('clicked', self._apply_bass)
        self.bass_apply.set_label('Применить')
        self.bass_apply.set_valign(Gtk.Align.CENTER)
        bass.add_suffix(self.bass_apply)

        diagnostics = Adw.PreferencesGroup(title='Диагностика')
        page.add(diagnostics)
        diagnostics.add(self.firmware_row)
        self.diagnostics = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.diagnostics.set_size_request(-1, 140)
        self.log_buffer = self.diagnostics.get_buffer()
        log_scroll = Gtk.ScrolledWindow(min_content_height=140, max_content_height=220, child=self.diagnostics)
        expander = Adw.ExpanderRow(title='Ответы BLE и ошибки', subtitle='Только просмотр')
        expander.add_row(log_scroll)
        diagnostics.add(expander)

        self.control_widgets = [refresh, pattern_row, self.pattern_apply, self.color_button,
            self.color_apply, self.auto_color, self.volume_scale, self.volume_apply,
            self.bass_row, self.bass_apply]
        self._set_controls()

    def _on_address_changed(self, entry):
        self.address = entry.get_text().strip()
        if hasattr(self, 'control_widgets'):
            self._set_controls()

    def _submit_startup(self, address):
        # Read audio output first, then begin BLE connect; routing is never implicit.
        self._submit('audio_status', address)
        self._connect_after_audio = address

    def _on_connect(self, *_):
        if self.connected:
            self._submit('disconnect')
        elif self.address:
            self._submit('connect', self.address)
        else:
            self._set_status('Укажи MAC-адрес сопряжённой PartyBox 710.', error=True)

    def _submit(self, method, *args):
        if self.busy or self.stopping.is_set():
            return
        if method not in ('connect', 'disconnect', 'audio_status', 'route_audio') and not self.connected:
            return
        if method in ('audio_status', 'route_audio') and not self.address:
            self._set_audio('Укажи MAC-адрес PartyBox 710 для проверки аудио.', error=True)
            return
        if method in ('audio_status', 'route_audio') and not args:
            args = (self.address,)
        self.busy = True
        if method == 'connect':
            self._set_status('Подключение и чтение состояния…')
        elif method in ('route_audio', 'audio_status'):
            self._set_audio('Проверяю звуковые выходы…')
        else:
            self._set_status('Выполняю запрос…')
        self._set_controls()
        self.tasks.put((method, args))

    def _work(self):
        device = None
        try:
            while not self.stopping.is_set():
                try:
                    method, args = self.tasks.get(timeout=.2)
                except queue.Empty:
                    continue
                try:
                    if method == 'connect':
                        if device:
                            device.close()
                        device = PartyBox(args[0])
                        result = device.connect()
                    elif method == 'disconnect':
                        if device:
                            device.close()
                        device = None
                        result = None
                    elif method in ('audio_status', 'route_audio'):
                        from .audio import get_audio_status, route_audio
                        result = (route_audio if method == 'route_audio' else get_audio_status)(args[0])
                    else:
                        if device is None:
                            raise RuntimeError('Управление колонкой не подключено')
                        result = getattr(device, method)(*args)
                    self.results.put(('ok', method, result))
                except Exception as exc:
                    if method not in ('audio_status', 'route_audio'):
                        if device:
                            device.close()
                        device = None
                    self.results.put(('error', method, str(exc)))
                GLib.idle_add(self._drain)
        finally:
            if device:
                device.close()

    def _drain(self):
        while True:
            try:
                kind, method, result = self.results.get_nowait()
            except queue.Empty:
                break
            self.busy = False
            if method in ('audio_status', 'route_audio'):
                if kind == 'error':
                    self._set_audio(f'Не удалось получить статус звука: {result}', error=True)
                    self._append_log(f'{method}: {result}')
                    if method == 'audio_status' and getattr(self, '_connect_after_audio', None):
                        address = self._connect_after_audio
                        self._connect_after_audio = None
                        GLib.idle_add(lambda: self._submit('connect', address) or GLib.SOURCE_REMOVE)
                else:
                    self.audio_available = bool(result.get('available'))
                    text = result.get('description') or ('Доступен' if self.audio_available else 'Звуковое устройство не найдено')
                    sink = result.get('sink')
                    if result.get('volume_percent') is not None:
                        text += f' · {result["volume_percent"]:.0f}%'
                    if result.get('is_default'):
                        text += ' · используется по умолчанию'
                    self._set_audio(text)
                    self._append_log(json.dumps(result, ensure_ascii=False, indent=2))
                if method == 'audio_status' and getattr(self, '_connect_after_audio', None):
                    address = self._connect_after_audio
                    self._connect_after_audio = None
                    GLib.idle_add(lambda: self._submit('connect', address) or GLib.SOURCE_REMOVE)
            elif kind == 'error':
                self.connected = False
                self.state = {}
                self._set_status(f'Ошибка: {result}', error=True)
                self._append_log(f'{method}: {result}')
            elif method == 'disconnect':
                self.connected = False
                self.state = {}
                self._set_status('Управление BLE отключено. Звук Linux не изменён.')
            else:
                self.connected = True
                self.state = result
                self._render(result)
                self._set_status('Подключено · состояние прочитано' if method in ('connect', 'refresh') else 'Изменение подтверждено ответом и чтением')
                self._append_log(json.dumps(result, ensure_ascii=False, indent=2))
                if method == 'refresh':
                    GLib.idle_add(lambda: self._submit('audio_status') or GLib.SOURCE_REMOVE)
            self._set_controls()
        return GLib.SOURCE_REMOVE

    def _set_status(self, text, error=False):
        self.ble_row.set_subtitle(text)
        self.ble_row.remove_css_class('error')
        if error:
            self.ble_row.add_css_class('error')

    def _set_audio(self, text, error=False):
        self.audio_row.set_subtitle(text)
        self.audio_row.remove_css_class('error')
        if error:
            self.audio_row.add_css_class('error')

    def _set_controls(self):
        active = self.connected and not self.busy
        for widget in self.control_widgets:
            widget.set_sensitive(active)
        self.connect_button.set_sensitive(not self.busy)
        self.connect_button.set_label('Отключить управление' if self.connected else 'Подключиться')
        self.address_entry.set_sensitive(not self.connected and not self.busy)
        self.audio_button.set_sensitive(bool(self.address) and not self.busy)
        self.audio_refresh_button.set_sensitive(bool(self.address) and not self.busy)
        self._updating_controls = True
        try:
            for tag, (row, switch) in self.zone_rows.items():
                value = self.state.get('zones', {}).get(f'{tag:02x}')
                switch.set_active(value == 1)
                switch.set_sensitive(active and value in (0, 1))
                row.set_subtitle(('Включено' if value else 'Выключено') if value in (0, 1) else 'Нет данных')
        finally:
            self._updating_controls = False

    def _render(self, state):
        self.firmware_row.set_subtitle(f'{state["address"]} · прошивка {state.get("firmware") or "не получена"}')
        pattern = state.get('pattern')
        index = list(PATTERNS).index(pattern) if pattern in PATTERNS else 0
        self.pattern_row.set_selected(index)
        if state.get('volume') is not None:
            self.volume_scale.set_value(state['volume'])
        self.bass_row.set_selected(state.get('bass') if state.get('bass') in (0, 1, 2) else 0)

    def _apply_pattern(self, *_):
        values = list(PATTERNS)
        value = values[self.pattern_row.get_selected()]
        if value == 7:
            self._confirm('Вспышки', 'Режим Flash использует световые вспышки. Включить его?', lambda: self._submit('set_pattern', value))
        else:
            self._submit('set_pattern', value)

    def _apply_color(self, *_):
        rgba = self.color_button.get_rgba()
        self._submit('set_color', [round(c * 255) for c in (rgba.red, rgba.green, rgba.blue)])

    def _apply_volume(self, *_):
        value = round(self.volume_scale.get_value())
        if value > 16 and value > (self.state.get('volume') or 0):
            self._confirm('Высокая громкость', f'Установить громкость {value} из 32?', lambda: self._submit('set_volume', value))
        else:
            self._submit('set_volume', value)

    def _apply_bass(self, *_):
        self._submit('set_bass', self.bass_row.get_selected())

    def _zone_toggle(self, switch, state, tag):
        if self.connected and not self.busy and not self._updating_controls:
            self._submit('set_zone', tag, bool(state))
        return False

    def _confirm(self, title, body, callback):
        dialog = Adw.AlertDialog(heading=title, body=body)
        dialog.add_response('cancel', 'Отмена')
        dialog.add_response('confirm', 'Продолжить')
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        dialog.connect('response', lambda _dialog, response: callback() if response == 'confirm' else None)
        dialog.present(self)

    def _append_log(self, text):
        end = self.log_buffer.get_end_iter()
        self.log_buffer.insert(end, text + '\n\n')
        count = self.log_buffer.get_char_count()
        if count > 40000:
            start = self.log_buffer.get_start_iter()
            trim = self.log_buffer.get_iter_at_offset(count - 30000)
            self.log_buffer.delete(start, trim)

    def _close(self, *_):
        if not self.stopping.is_set():
            self.stopping.set()
            self.set_visible(False)
            GLib.timeout_add(100, self._finish_close)
        return True

    def _finish_close(self):
        if self.worker.is_alive():
            return GLib.SOURCE_CONTINUE
        self.destroy()
        return GLib.SOURCE_REMOVE
