# -*- coding: utf-8 -*-
from PyQt5.QtCore import Qt, QPoint
from PyQt5.QtWidgets import QDialog, QMessageBox
from config import save_settings
from .base_mixin import BaseMixin
from constants import DEFAULT_SAVE_DIR

class SettingsMixin(BaseMixin):
    """设置对话框"""

    def open_settings(self):
        from widgets import SettingsDialog   # 延迟导入
        dlg = SettingsDialog(None)
        dlg.setWindowModality(Qt.ApplicationModal)

        dlg.spin_limit.setValue(self.settings.get('limit', 10))
        dlg.check_dedup.setChecked(self.settings.get('dedup', False))
        current_theme = self.settings.get('theme', 'light')
        dlg.theme_combo.setCurrentIndex(0 if current_theme == 'light' else 1)
        dlg.path_edit.setText(self.settings.get('save_dir', DEFAULT_SAVE_DIR))
        dlg.format_combo.setCurrentText(self.settings.get('filename_format', '歌手-歌曲名'))
        dlg.format_custom_edit.setText(self.settings.get('custom_format', ''))
        dlg.check_lyric.setChecked(self.settings.get('download_lyric', True))
        dlg.check_cover.setChecked(self.settings.get('download_cover', True))
        dlg.check_embed_lyrics.setChecked(self.settings.get('embed_lyrics', False))
        dlg.check_delete_lyrics.setChecked(self.settings.get('delete_lyrics', False))
        dlg.check_embed_cover.setChecked(self.settings.get('embed_cover', False))
        dlg.check_delete_cover.setChecked(self.settings.get('delete_cover', False))

        group_by = self.settings.get('group_by', '无分组')
        index = dlg.group_combo.findText(group_by)
        if index >= 0:
            dlg.group_combo.setCurrentIndex(index)

        for cb in dlg.source_checkboxes:
            cb.setChecked(cb.text() in self.settings.get('sources', []))

        dlg.convert_check.setChecked(self.settings.get('convert_enabled', False))
        dlg.convert_combo.setCurrentText(self.settings.get('convert_format', ''))
        dlg.bitrate_combo.setCurrentText(self.settings.get('convert_bitrate', ''))
        dlg.convert_combo.setEnabled(dlg.convert_check.isChecked())
        dlg.bitrate_combo.setEnabled(dlg.convert_check.isChecked())

        dlg.opacity_slider.setValue(int(self.settings.get('background_opacity', 0.8) * 100))

        parent_geo = self.geometry()
        dlg.move(
            parent_geo.x() + (parent_geo.width() - 620) // 2,
            parent_geo.y() + (parent_geo.height() - 330) // 2
        )

        if dlg.exec_() == QDialog.Accepted:
            self.settings['volume'] = self.slider_volume.value()
            new_settings = dlg.get_settings()
            self.settings.update(new_settings)
            save_settings(self.settings)
            self.apply_theme(self.settings.get('theme', 'light'))
            # 重新初始化客户端（下次使用时自动重新创建）
            self.music_client = None
            self.refresh_client = None
            self._apply_volume_from_settings()
            self.label_stats.setText("设置已更新并保存")
