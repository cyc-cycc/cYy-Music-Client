# -*- coding: utf-8 -*-
import os
import sys
from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QFont
from utils import setup_runtime_paths, get_global_stylesheet
from gui import MusicdlGUI

try:
    import pyi_splash
except ImportError:
    pyi_splash = None

setup_runtime_paths()

if __name__ == '__main__':
    if sys.platform == 'darwin':
        try:
            test_file = os.path.join(os.getcwd(), '.write_test')
            with open(test_file, 'w') as f:
                f.write('test')
            os.remove(test_file)
        except OSError:
            os.chdir(os.path.expanduser("~"))

    app = QApplication(sys.argv)
    font = QFont("Microsoft YaHei", 10)
    if sys.platform == 'darwin':
        font.setFamily("PingFang SC")
    app.setFont(font)
    app.setStyleSheet(get_global_stylesheet())

    gui = MusicdlGUI()
    gui.show()
    
    # 关闭启动动画
    if pyi_splash is not None:
        pyi_splash.close()

    sys.exit(app.exec_())
