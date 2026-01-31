import sys
import os
import subprocess
import json
import shutil
from datetime import timedelta

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QLabel, QSlider, QFileDialog,
                             QGroupBox, QSpinBox, QComboBox, QCheckBox, QProgressBar,
                             QMessageBox, QLineEdit, QRadioButton, QButtonGroup, QScrollArea, 
                             QDoubleSpinBox, QStyle, QSizePolicy, QFrame, QGridLayout)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QUrl, QSize, QObject
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtGui import QFont, QPalette, QColor, QDragEnterEvent, QDropEvent, QIcon, QAction

# --- 工具函式 ---
def get_tool_path(filename):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, filename)
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    local_path = os.path.join(script_dir, filename)
    
    if os.path.exists(local_path):
        return local_path
    
    system_path = shutil.which(filename)
    if system_path:
        return system_path
        
    return filename

# --- GPU 檢測執行緒 ---
class GPUCheckThread(QThread):
    finished = pyqtSignal(str, str)

    def run(self):
        report = []
        available_vendors = []
        ffmpeg_path = get_tool_path("ffmpeg.exe")

        report.append("【硬體偵測】")
        try:
            ps_cmd = "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name"
            cmd = ["powershell", "-NoProfile", "-Command", ps_cmd]
            
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            
            result = subprocess.run(cmd, capture_output=True, text=True, startupinfo=startupinfo)
            
            if result.returncode == 0:
                gpus = [line.strip() for line in result.stdout.split('\n') if line.strip()]
                for gpu in gpus:
                    report.append(f"• {gpu}")
            else:
                report.append("無法讀取硬體列表")
        except Exception as e:
            report.append(f"硬體讀取錯誤: {e}")
        
        report.append("\n【加速功能診斷】")
        
        if not os.path.exists(ffmpeg_path) and not shutil.which("ffmpeg"):
             report.append(f"❌ 嚴重警告: 找不到 ffmpeg.exe！\n搜尋路徑: {os.path.dirname(ffmpeg_path)}")

        tests = [
            ("NVIDIA", "h264_nvenc"),
            ("AMD", "h264_amf"),
            ("Intel", "h264_qsv")
        ]

        for vendor, encoder in tests:
            test_cmd = [
                ffmpeg_path, 
                '-y', 
                '-v', 'error',
                '-f', 'lavfi', '-i', 'color=s=1920x1080:d=1',
                '-c:v', encoder,
                '-f', 'null', '-'
            ]
            
            try:
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                
                proc = subprocess.run(test_cmd, capture_output=True, text=True, startupinfo=startupinfo)
                
                if proc.returncode == 0:
                    report.append(f"✅ {vendor}: 支援 (可用)")
                    available_vendors.append(vendor)
                else:
                    err_msg = proc.stderr.strip() if proc.stderr else "未知錯誤"
                    if "device not found" in err_msg:
                        report.append(f"❌ {vendor}: 未偵測到對應硬體")
                    else:
                        report.append(f"❌ {vendor}: 測試未通過")
            except Exception:
                report.append(f"❌ {vendor}: 執行失敗")

        rec_vendor = "CPU"
        report.append("\n【結果建議】")
        if "NVIDIA" in available_vendors:
            rec_vendor = "NVIDIA"
            report.append("★ 偵測到 NVIDIA 顯卡，已自動選擇最佳效能模式。")
        elif "AMD" in available_vendors:
            rec_vendor = "AMD"
            report.append("★ 偵測到 AMD 顯卡，已自動切換至加速模式。")
        elif "Intel" in available_vendors:
            rec_vendor = "Intel"
            report.append("★ 偵測到 Intel 加速 (QuickSync)，已自動切換。")
        else:
            report.append("未偵測到可用的硬體加速，建議使用 CPU 模式。")

        final_msg = "\n".join(report)
        self.finished.emit(final_msg, rec_vendor)

# --- 可點擊跳轉的 Slider ---
class ClickableSlider(QSlider):
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            val = self.minimum() + (self.maximum() - self.minimum()) * event.position().x() / self.width()
            self.setValue(int(val))
            self.sliderMoved.emit(int(val))
            event.accept()
        super().mousePressEvent(event)

# --- 影片處理執行緒 ---
class VideoProcessor(QThread):
    progress = pyqtSignal(int)
    finished = pyqtSignal(bool, str)
    
    def __init__(self, input_path, output_path, start_time, end_time, 
                 mode='copy', quality=23, fps=None, bitrate=None, 
                 output_format='mp4', resolution=None, gpu_vendor='CPU', speed=1.0):
        super().__init__()
        self.input_path = input_path
        self.output_path = output_path
        self.start_time = start_time
        self.end_time = end_time
        self.mode = mode
        self.quality = quality
        self.fps = fps
        self.bitrate = bitrate
        self.output_format = output_format
        self.resolution = resolution
        self.gpu_vendor = gpu_vendor
        self.speed = speed
        self.process = None
        
    def run(self):
        try:
            duration = self.end_time - self.start_time
            ffmpeg_path = get_tool_path("ffmpeg.exe")
            
            cmd = [ffmpeg_path]
            
            # 硬體解碼
            if self.gpu_vendor == 'NVIDIA':
                cmd.extend(['-hwaccel', 'cuda'])
            elif self.gpu_vendor == 'Intel':
                cmd.extend(['-hwaccel', 'qsv'])
            elif self.gpu_vendor == 'AMD':
                cmd.extend(['-hwaccel', 'dxva2']) 
            
            cmd.extend(['-ss', str(self.start_time)])
            cmd.extend(['-i', self.input_path])
            cmd.extend(['-t', str(duration)])
            
            if self.mode == 'copy':
                cmd.extend(['-c', 'copy'])
            else:
                # --- [全顯卡完美修正版] 編碼器設定 ---
                if self.gpu_vendor == 'NVIDIA':
                    cmd.extend(['-c:v', 'h264_nvenc', '-preset', 'p4'])
                    if not self.bitrate:
                        if self.quality == 0:
                            # NVIDIA: 0 = Auto(爛畫質)，所以必須強制用 constqp 0 (無損)
                            cmd.extend(['-rc', 'constqp', '-qp', '0'])
                        else:
                            # NVIDIA: 非 0 時用 VBR，並解除碼率上限
                            cmd.extend(['-rc', 'vbr', '-cq', str(self.quality), '-b:v', '0'])
                
                elif self.gpu_vendor == 'AMD':
                    cmd.extend(['-c:v', 'h264_amf', '-usage', 'transcoding'])
                    if not self.bitrate:
                        # AMD: 0 就是 0 (無損)，直接用沒問題
                        cmd.extend(['-rc', 'cqp', '-qp_i', str(self.quality), '-qp_p', str(self.quality)])
                
                elif self.gpu_vendor == 'Intel':
                    cmd.extend(['-c:v', 'h264_qsv', '-preset', 'medium'])
                    if not self.bitrate:
                        # Intel: 範圍通常是 1-51，0 可能會無效，所以遇到 0 我們改成 1 (最高畫質)
                        q_val = 1 if self.quality == 0 else self.quality
                        cmd.extend(['-global_quality', str(q_val)])
                
                else: # CPU (x264)
                    cmd.extend(['-c:v', 'libx264', '-preset', 'medium'])
                    if not self.bitrate:
                        # CPU: 0 代表無損，直接用沒問題
                        cmd.extend(['-crf', str(self.quality)])
                
                # 碼率設定 (若有勾選，這會覆蓋上面的 CRF 設定)
                if self.bitrate:
                    b_val = f'{self.bitrate}k'
                    cmd.extend(['-b:v', b_val, '-maxrate', b_val, '-bufsize', f'{self.bitrate * 2}k'])
                
                # 濾鏡與音訊處理
                video_filters = []
                if self.resolution:
                    video_filters.append(f'scale={self.resolution}')
                if self.speed != 1.0:
                    video_filters.append(f'setpts={1/self.speed}*PTS')
                
                if video_filters:
                    cmd.extend(['-vf', ','.join(video_filters)])
                
                cmd.extend(['-c:a', 'aac', '-b:a', '192k'])
                if self.speed != 1.0:
                    cmd.extend(['-af', f'atempo={self.speed}'])
                
                if self.fps:
                    cmd.extend(['-r', str(self.fps)])
            
            cmd.extend(['-y', self.output_path])
            print("執行指令:", " ".join(cmd))

            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW if os.name == 'nt' else 0

            self.process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True, encoding='utf-8', errors='replace',
                startupinfo=startupinfo
            )
            
            # 進度條... (以下省略，維持原樣)
            for line in self.process.stderr:
                if 'time=' in line:
                    try:
                        time_str = line.split('time=')[1].split()[0]
                        h, m, s = time_str.split(':')
                        current = int(h) * 3600 + int(m) * 60 + float(s)
                        expected_duration = duration / self.speed
                        if expected_duration > 0:
                            progress_percent = min(int((current / expected_duration) * 100), 100)
                            self.progress.emit(progress_percent)
                    except:
                        pass
            
            self.process.wait()
            
            if self.process.returncode == 0:
                self.finished.emit(True, "影片剪輯完成！")
            else:
                self.finished.emit(False, "剪輯錯誤，請檢查設定或硬體支援。")
                
        except Exception as e:
            self.finished.emit(False, f"錯誤: {str(e)}")
    
    def _time_to_seconds(self, time_str):
        try:
            h, m, s = time_str.split(':')
            return int(h) * 3600 + int(m) * 60 + float(s)
        except:
            return 0
    
    def stop(self):
        if self.process:
            self.process.terminate()

# --- 主視窗 ---
class VideoCutter(QMainWindow):
    def __init__(self):
        super().__init__()
        self.video_path = None
        self.video_duration = 0
        self.video_bitrate = 0
        self.processor = None
        self.gpu_checker = None 
        
        self.setAcceptDrops(True)
        self.initUI()
        
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
    
    def dropEvent(self, event: QDropEvent):
        files = [u.toLocalFile() for u in event.mimeData().urls()]
        if files:
            video_extensions = ['.mp4', '.mkv', '.avi', '.mov', '.webm', 
                              '.flv', '.wmv', '.m4v', '.mpg', '.mpeg']
            for file in files:
                if any(file.lower().endswith(ext) for ext in video_extensions):
                    self.load_video(file)
                    break
        
    def initUI(self):
        self.setWindowTitle('影片剪輯工具') # 修改標題
        self.setGeometry(100, 100, 1400, 900)
        
        icon_path = get_tool_path("app.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        self.setStyleSheet("""
            QMainWindow { background-color: #1e1e1e; }
            QLabel { color: #ffffff; font-size: 12px; }
            QPushButton { background-color: #0d7377; color: white; border: none; padding: 6px 12px; border-radius: 4px; font-weight: bold; }
            QPushButton:hover { background-color: #14a085; }
            QPushButton:pressed { background-color: #0a5a5d; }
            QGroupBox { color: #ffffff; border: 1px solid #3d3d3d; border-radius: 6px; margin-top: 10px; font-weight: bold; padding-top: 10px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
            QSlider::groove:horizontal { background: #3d3d3d; height: 6px; border-radius: 3px; }
            QSlider::handle:horizontal { background: #0d7377; width: 14px; margin: -4px 0; border-radius: 7px; }
            QSlider::handle:horizontal:hover { background: #14a085; }
            QComboBox, QSpinBox, QLineEdit, QDoubleSpinBox { background-color: #2d2d2d; color: white; border: 1px solid #3d3d3d; padding: 4px; border-radius: 3px; }
            QComboBox::drop-down { border: none; }
            QRadioButton { color: white; font-size: 12px; }
            QProgressBar { border: 1px solid #3d3d3d; border-radius: 4px; text-align: center; color: white; }
            QProgressBar::chunk { background-color: #0d7377; border-radius: 3px; }
            QCheckBox { color: white; spacing: 5px; }
            QLabel#DropLabel {
                border: 2px dashed #555;
                border-radius: 10px;
                color: #aaa;
                font-size: 14px;
            }
            QLabel#DropLabel:hover {
                border: 2px dashed #0d7377;
                color: #0d7377;
                background-color: #252525;
            }
            QScrollArea { background-color: transparent; border: none; }
            QWidget#RightPanelContent { background-color: transparent; }
            
            /* --- [新增] 檔案資訊卡片樣式 --- */
            QLabel#InfoTitle { color: #888; font-size: 11px; font-weight: normal; }
            QLabel#InfoValue { color: #00dede; font-size: 13px; font-weight: bold; }
            
            /* --- [新增] 進階設定外框樣式 --- */
            QFrame#AdvancedFrame {
                border: 1px solid #555555; /* 白色/深灰細框 */
                border-radius: 6px;
                background-color: #252525; 
            }
        """)
        
        self.arrow_style = """
            QPushButton {
                background-color: #0d7377;
                color: white;
                border: none;
                padding: 0px; 
                border-radius: 4px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #14a085; }
            QPushButton:pressed { background-color: #0a5a5d; }
        """
        
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        main_layout = QHBoxLayout(central_widget)
        main_layout.setSpacing(15)
        main_layout.setContentsMargins(15, 15, 15, 15)
        
        # --- 左側面板 ---
        left_panel = QVBoxLayout()
        
        self.video_widget = QVideoWidget()
        self.video_widget.setStyleSheet("background-color: #000000; border-radius: 5px;")
        self.video_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        left_panel.addWidget(self.video_widget)
        
        self.media_player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.media_player.setAudioOutput(self.audio_output)
        self.media_player.setVideoOutput(self.video_widget)
        self.media_player.positionChanged.connect(self.update_position)
        self.media_player.durationChanged.connect(self.update_duration)
        
        timeline_group = QGroupBox("時間軸與裁切")
        grid = QGridLayout()
        grid.setSpacing(10)
        grid.setContentsMargins(15, 15, 15, 15)
        
        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 1) 
        grid.setColumnStretch(2, 0)

        # Row 0: 微調精度 & 總長度
        grid.addWidget(QLabel("微調精度:"), 0, 0)
        self.step_combo = QComboBox() 
        self.step_combo.addItems(['0.1 秒', '0.3 秒', '0.5 秒', '1.0 秒'])
        self.step_combo.setFixedWidth(80)
        self.step_combo.setCurrentIndex(2)
        grid.addWidget(self.step_combo, 0, 1, Qt.AlignmentFlag.AlignLeft)
        
        self.range_info_label = QLabel("總長度: 00:00:00")
        self.range_info_label.setStyleSheet("color: #ffd60a; font-weight: bold;")
        grid.addWidget(self.range_info_label, 0, 2, Qt.AlignmentFlag.AlignRight)

        # Row 1: 預覽進度
        grid.addWidget(QLabel("預覽進度:"), 1, 0)
        
        self.position_slider = ClickableSlider(Qt.Orientation.Horizontal)
        self.position_slider.sliderMoved.connect(self.set_position)
        self.position_slider.setEnabled(False)
        grid.addWidget(self.position_slider, 1, 1)
        
        row1_controls = QWidget()
        row1_layout = QHBoxLayout(row1_controls)
        row1_layout.setContentsMargins(0, 0, 0, 0)
        row1_layout.setSpacing(5)
        
        self.duration_label = QLabel("00:00:00")
        self.duration_label.setFixedWidth(55)
        self.duration_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.step_back_btn = QPushButton("<<")
        self.step_back_btn.setFixedSize(30, 25)
        self.step_back_btn.setStyleSheet(self.arrow_style)
        self.step_back_btn.clicked.connect(lambda: self.step_video('back'))
        
        self.play_btn = QPushButton("▶ 播放")
        self.play_btn.setFixedSize(70, 25)
        self.play_btn.clicked.connect(self.toggle_play)
        
        self.step_fwd_btn = QPushButton(">>")
        self.step_fwd_btn.setFixedSize(30, 25)
        self.step_fwd_btn.setStyleSheet(self.arrow_style)
        self.step_fwd_btn.clicked.connect(lambda: self.step_video('fwd'))

        self.preview_speed_check = QCheckBox("2倍速")
        self.preview_speed_check.setStyleSheet("color: #ffd60a; font-weight: bold;")
        self.preview_speed_check.stateChanged.connect(self.toggle_preview_speed)
        
        self.current_time_label = QLabel("00:00:00.000")
        self.current_time_label.setStyleSheet("color: #0d7377; font-weight: bold;")
        self.current_time_label.setFixedWidth(85)
        self.current_time_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        row1_layout.addWidget(self.duration_label)
        row1_layout.addWidget(self.step_back_btn)
        row1_layout.addWidget(self.play_btn)
        row1_layout.addWidget(self.step_fwd_btn)
        row1_layout.addWidget(self.preview_speed_check)
        row1_layout.addWidget(self.current_time_label)
        
        grid.addWidget(row1_controls, 1, 2)

        # Row 2: 開始時間
        grid.addWidget(QLabel("開始時間:"), 2, 0)
        self.start_slider = ClickableSlider(Qt.Orientation.Horizontal)
        self.start_slider.valueChanged.connect(self.update_range_labels)
        self.start_slider.setEnabled(False)
        grid.addWidget(self.start_slider, 2, 1)
        
        row2_controls = QWidget()
        row2_layout = QHBoxLayout(row2_controls)
        row2_layout.setContentsMargins(0, 0, 0, 0)
        row2_layout.setSpacing(5)
        
        self.start_time_label = QLabel("00:00:00")
        self.start_time_label.setFixedWidth(55)
        self.start_time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.start_time_label.setStyleSheet("color: #14a085; font-weight: bold;")
        
        self.start_back_btn = QPushButton("<<")
        self.start_back_btn.setFixedSize(30, 25)
        self.start_back_btn.setStyleSheet(self.arrow_style)
        self.start_back_btn.clicked.connect(lambda: self.adjust_range_time('start', 'back'))

        self.start_fwd_btn = QPushButton(">>")
        self.start_fwd_btn.setFixedSize(30, 25)
        self.start_fwd_btn.setStyleSheet(self.arrow_style)
        self.start_fwd_btn.clicked.connect(lambda: self.adjust_range_time('start', 'fwd'))
        
        set_start_btn = QPushButton("設為當前")
        set_start_btn.setFixedSize(70, 25)
        set_start_btn.clicked.connect(lambda: self.set_from_current('start'))
        
        self.jump_start_btn = QPushButton("跳至此點")
        self.jump_start_btn.setFixedSize(70, 25)
        self.jump_start_btn.setStyleSheet("background-color: #5c2b2b; color: white;")
        self.jump_start_btn.clicked.connect(lambda: self.seek_to_range('start'))
        
        row2_layout.addWidget(self.start_time_label)
        row2_layout.addWidget(self.start_back_btn)
        row2_layout.addWidget(self.start_fwd_btn)
        row2_layout.addWidget(set_start_btn)
        row2_layout.addWidget(self.jump_start_btn)
        row2_layout.addStretch() 
        
        grid.addWidget(row2_controls, 2, 2)

        # Row 3: 結束時間
        grid.addWidget(QLabel("結束時間:"), 3, 0)
        self.end_slider = ClickableSlider(Qt.Orientation.Horizontal)
        self.end_slider.valueChanged.connect(self.update_range_labels)
        self.end_slider.setEnabled(False)
        grid.addWidget(self.end_slider, 3, 1)
        
        row3_controls = QWidget()
        row3_layout = QHBoxLayout(row3_controls)
        row3_layout.setContentsMargins(0, 0, 0, 0)
        row3_layout.setSpacing(5)
        
        self.end_time_label = QLabel("00:00:00")
        self.end_time_label.setFixedWidth(55)
        self.end_time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.end_time_label.setStyleSheet("color: #14a085; font-weight: bold;")
        
        self.end_back_btn = QPushButton("<<")
        self.end_back_btn.setFixedSize(30, 25)
        self.end_back_btn.setStyleSheet(self.arrow_style)
        self.end_back_btn.clicked.connect(lambda: self.adjust_range_time('end', 'back'))

        self.end_fwd_btn = QPushButton(">>")
        self.end_fwd_btn.setFixedSize(30, 25)
        self.end_fwd_btn.setStyleSheet(self.arrow_style)
        self.end_fwd_btn.clicked.connect(lambda: self.adjust_range_time('end', 'fwd'))
        
        set_end_btn = QPushButton("設為當前")
        set_end_btn.setFixedSize(70, 25)
        set_end_btn.clicked.connect(lambda: self.set_from_current('end'))
        
        self.jump_end_btn = QPushButton("跳至此點")
        self.jump_end_btn.setFixedSize(70, 25)
        self.jump_end_btn.setStyleSheet("background-color: #5c2b2b; color: white;")
        self.jump_end_btn.clicked.connect(lambda: self.seek_to_range('end'))
        
        row3_layout.addWidget(self.end_time_label)
        row3_layout.addWidget(self.end_back_btn)
        row3_layout.addWidget(self.end_fwd_btn)
        row3_layout.addWidget(set_end_btn)
        row3_layout.addWidget(self.jump_end_btn)
        row3_layout.addStretch()

        grid.addWidget(row3_controls, 3, 2)
        
        timeline_group.setLayout(grid)
        left_panel.addWidget(timeline_group)
        main_layout.addLayout(left_panel, 3)

        # --- 右側面板 ---
        right_panel_content = QWidget()
        right_panel_content.setObjectName("RightPanelContent")
        right_panel_layout = QVBoxLayout(right_panel_content)
        right_panel_layout.setSpacing(15)
        right_panel_layout.setContentsMargins(0,0,10,0)
        
        self.file_drop_area = QPushButton()
        self.file_drop_area.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.file_drop_area.setMinimumHeight(280) 
        self.file_drop_area.setCursor(Qt.CursorShape.PointingHandCursor)
        self.file_drop_area.clicked.connect(self.select_video)
        
        drop_layout = QVBoxLayout(self.file_drop_area)
        folder_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon)
        icon_label = QLabel()
        icon_label.setPixmap(folder_icon.pixmap(100, 100)) 
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_layout.addWidget(icon_label)
        
        self.file_text_label = QLabel("點擊選擇影片\n或將檔案拖放至此")
        self.file_text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.file_text_label.setStyleSheet("color: #ccc; font-weight: bold; font-size: 16px;")
        drop_layout.addWidget(self.file_text_label)
        
        self.file_drop_area.setObjectName("DropLabel")
        right_panel_layout.addWidget(self.file_drop_area)
        
        # --- [優化] 檔案資訊卡片 4-Grid ---
        self.file_info_box = QGroupBox("檔案資訊")
        self.file_info_box.setVisible(False) # 預設隱藏
        info_grid = QGridLayout()
        info_grid.setContentsMargins(10, 10, 10, 10)
        info_grid.setSpacing(5)
        
        self.lbl_res_val = QLabel("-")
        self.lbl_fps_val = QLabel("-")
        self.lbl_bitrate_val = QLabel("-")
        self.lbl_dur_val = QLabel("-")
        
        for lbl in [self.lbl_res_val, self.lbl_fps_val, self.lbl_bitrate_val, self.lbl_dur_val]:
            lbl.setObjectName("InfoValue")

        # Row 0
        info_grid.addWidget(QLabel("解析度:", objectName="InfoTitle"), 0, 0)
        info_grid.addWidget(self.lbl_res_val, 0, 1)
        info_grid.addWidget(QLabel("幀率:", objectName="InfoTitle"), 0, 2)
        info_grid.addWidget(self.lbl_fps_val, 0, 3)
        
        # Row 1
        info_grid.addWidget(QLabel("碼率:", objectName="InfoTitle"), 1, 0)
        info_grid.addWidget(self.lbl_bitrate_val, 1, 1)
        info_grid.addWidget(QLabel("時長:", objectName="InfoTitle"), 1, 2)
        info_grid.addWidget(self.lbl_dur_val, 1, 3)
        
        self.file_info_box.setLayout(info_grid)
        right_panel_layout.addWidget(self.file_info_box)
        
        self.info_label = QLabel("") # 保留變數但不顯示，防錯
        self.info_label.setVisible(False)
        right_panel_layout.addStretch()
        
        # --- 輸出設定 (含白色細框) ---
        settings_group = QGroupBox("輸出設定")
        settings_layout = QVBoxLayout()
        settings_layout.setSpacing(10)
        settings_layout.setContentsMargins(15, 20, 15, 15)
        
        # Mode Selection
        self.mode_group_btn = QButtonGroup()
        self.copy_mode_radio = QRadioButton("極速剪輯 (無損)")
        self.copy_mode_radio.setChecked(True)
        self.copy_mode_radio.toggled.connect(self.toggle_mode_options)
        self.mode_group_btn.addButton(self.copy_mode_radio)
        settings_layout.addWidget(self.copy_mode_radio)
        
        self.compress_mode_radio = QRadioButton("進階模式")
        self.compress_mode_radio.toggled.connect(self.toggle_mode_options)
        self.mode_group_btn.addButton(self.compress_mode_radio)
        settings_layout.addWidget(self.compress_mode_radio)
        
        # --- [關鍵修改] Advanced Frame 外框 ---
        self.compress_widget = QFrame()
        self.compress_widget.setObjectName("AdvancedFrame")
        
        comp_grid = QGridLayout(self.compress_widget)
        comp_grid.setContentsMargins(15, 15, 15, 15)
        comp_grid.setSpacing(10)
        comp_grid.setColumnStretch(1, 1)
        
        def make_lbl(text):
            l = QLabel(text)
            l.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            l.setStyleSheet("font-size: 13px; color: #ddd;")
            return l
            
        def make_desc(text):
            l = QLabel(text)
            l.setStyleSheet("color: #888; font-size: 12px;")
            l.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            return l
        
        # Row 0: 加速
        comp_grid.addWidget(make_lbl("加速:"), 0, 0)
        self.gpu_combo = QComboBox()
        self.gpu_combo.addItems(['CPU', 'NVIDIA', 'AMD', 'Intel'])
        comp_grid.addWidget(self.gpu_combo, 0, 1)
        comp_grid.addWidget(make_desc("使用顯卡硬體加速轉檔"), 0, 2)
        
        # Row 1: 倍速
        comp_grid.addWidget(make_lbl("倍速:"), 1, 0)
        self.speed_spin = QDoubleSpinBox()
        self.speed_spin.setRange(0.5, 2.0)
        self.speed_spin.setSingleStep(0.1)
        self.speed_spin.setValue(1.0)
        self.speed_spin.setSuffix("x")
        comp_grid.addWidget(self.speed_spin, 1, 1)
        comp_grid.addWidget(make_desc("調整影片播放速度"), 1, 2)
        
        # Row 2: 分辨率
        comp_grid.addWidget(make_lbl("分辨率:"), 2, 0)
        self.resolution_combo = QComboBox()
        self.resolution_combo.addItems(['原始', '4K', '2K', '1080p', '720p'])
        comp_grid.addWidget(self.resolution_combo, 2, 1)
        comp_grid.addWidget(make_desc("設定輸出影片解析度"), 2, 2)
        
        # Row 3: CRF
        comp_grid.addWidget(make_lbl("CRF:"), 3, 0)
        self.quality_spin = QSpinBox()
        self.quality_spin.setRange(0, 51)
        self.quality_spin.setValue(23)
        comp_grid.addWidget(self.quality_spin, 3, 1)
        comp_grid.addWidget(make_desc("數值越小畫質越好 (範圍 0~51)"), 3, 2)
        
        # --- [修改] Row 4: FPS 獨立一行 + 說明 ---
        self.fps_check = QCheckBox("FPS")
        self.fps_check.setLayoutDirection(Qt.LayoutDirection.RightToLeft) # 讓勾選框靠右對齊文字
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(1, 240)
        self.fps_spin.setValue(60)
        self.fps_spin.setEnabled(False)
        self.fps_check.stateChanged.connect(lambda: self.fps_spin.setEnabled(self.fps_check.isChecked()))
        
        comp_grid.addWidget(self.fps_check, 4, 0, Qt.AlignmentFlag.AlignRight)
        comp_grid.addWidget(self.fps_spin, 4, 1)
        comp_grid.addWidget(make_desc("強制設定影片幀率 (如 60fps)"), 4, 2) 
        
        # --- [修改] Row 5: 碼率 獨立一行 + 說明 ---
        self.bitrate_check = QCheckBox("碼率")
        self.bitrate_check.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.bitrate_spin = QSpinBox()
        self.bitrate_spin.setRange(1000, 500000)
        self.bitrate_spin.setValue(10000)
        self.bitrate_spin.setEnabled(False)
        self.bitrate_check.stateChanged.connect(lambda: self.bitrate_spin.setEnabled(self.bitrate_check.isChecked()))
        
        comp_grid.addWidget(self.bitrate_check, 5, 0, Qt.AlignmentFlag.AlignRight)
        comp_grid.addWidget(self.bitrate_spin, 5, 1)
        comp_grid.addWidget(make_desc("控制影片數據流量 (kbps)"), 5, 2)
        
        settings_layout.addWidget(self.compress_widget)
        
        # --- [新增] 格式選擇與 GPU 按鈕並排區塊 (移出 Frame 放在下面) ---
        format_gpu_row = QHBoxLayout()
        format_gpu_row.setContentsMargins(5, 5, 5, 5)
        
        # 左側: 格式選擇
        fmt_layout = QHBoxLayout()
        fmt_lbl = QLabel("格式:")
        fmt_lbl.setStyleSheet("font-size: 14px; font-weight: bold;")
        fmt_layout.addWidget(fmt_lbl)
        
        self.format_combo = QComboBox()
        self.format_combo.addItems(['mp4', 'mkv', 'avi', 'mov'])
        self.format_combo.setMinimumWidth(120)
        fmt_layout.addWidget(self.format_combo)
        
        format_gpu_row.addLayout(fmt_layout)
        format_gpu_row.addStretch() # 中間推開
        
        # 右側: 檢測按鈕 (從 Frame 移出來放在這裡)
        self.detect_btn = QPushButton("檢測是否支持硬體加速")
        self.detect_btn.setFixedSize(180, 32)
        self.detect_btn.setStyleSheet("background-color: #5c2b2b; color: white; border-radius: 4px; font-weight: bold;")
        self.detect_btn.clicked.connect(self.start_gpu_check)
        
        format_gpu_row.addWidget(self.detect_btn)
        
        settings_layout.addLayout(format_gpu_row)
        
        settings_group.setLayout(settings_layout)
        right_panel_layout.addWidget(settings_group)
        
        # 4. 底部按鈕區 (簡化: 移除原本的格式選擇，只留預估大小與開始按鈕)
        bottom_box = QWidget()
        bottom_layout = QVBoxLayout(bottom_box)
        bottom_layout.setContentsMargins(0, 10, 0, 0)
        
        self.size_estimate_label = QLabel("預估大小: -")
        self.size_estimate_label.setStyleSheet("color: #ffd60a; font-weight: bold; margin-bottom: 5px;")
        self.size_estimate_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bottom_layout.addWidget(self.size_estimate_label)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        bottom_layout.addWidget(self.progress_bar)
        
        self.process_btn = QPushButton("開始剪輯")
        self.process_btn.setMinimumHeight(50)
        self.process_btn.clicked.connect(self.process_video)
        self.process_btn.setEnabled(False)
        self.process_btn.setStyleSheet("""
            QPushButton { background-color: #d62828; font-size: 18px; border-radius: 6px; }
            QPushButton:hover { background-color: #f23535; }
            QPushButton:disabled { background-color: #555555; }
        """)
        bottom_layout.addWidget(self.process_btn)
        
        right_panel_layout.addWidget(bottom_box)

        right_scroll_area = QScrollArea()
        right_scroll_area.setWidgetResizable(True)
        right_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right_scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        right_scroll_area.setWidget(right_panel_content)
        
        main_layout.addWidget(right_scroll_area, 1)
        
        self.setup_connections()
        self.toggle_mode_options() # 初始化 UI 狀態
        self.showMaximized()
        
    def setup_connections(self):
        self.start_slider.valueChanged.connect(self.estimate_file_size)
        self.end_slider.valueChanged.connect(self.estimate_file_size)
        self.copy_mode_radio.toggled.connect(self.estimate_file_size)
        self.resolution_combo.currentIndexChanged.connect(self.estimate_file_size)
        self.quality_spin.valueChanged.connect(self.estimate_file_size)
        self.bitrate_check.stateChanged.connect(self.estimate_file_size)
        self.bitrate_spin.valueChanged.connect(self.estimate_file_size)
        self.speed_spin.valueChanged.connect(self.estimate_file_size)

    def start_gpu_check(self):
        self.detect_btn.setText("檢測中...")
        self.detect_btn.setEnabled(False)
        self.gpu_checker = GPUCheckThread()
        self.gpu_checker.finished.connect(self.on_gpu_check_finished)
        self.gpu_checker.start()

    def on_gpu_check_finished(self, report_text, rec_vendor):
        self.detect_btn.setText("檢測是否支持硬體加速")
        self.detect_btn.setEnabled(True)
        
        msg = QMessageBox(self)
        msg.setWindowTitle("硬體檢測報告")
        msg.setText(report_text)
        msg.setStyleSheet("QLabel{min-width: 400px; font-size: 13px;}")
        msg.exec()
        
        index = self.gpu_combo.findText(rec_vendor)
        if index >= 0:
            self.gpu_combo.setCurrentIndex(index)

    def toggle_mode_options(self):
        enable_adv = self.compress_mode_radio.isChecked()
        
        # 這些是要鎖定的控制項
        controls_to_toggle = [
            self.gpu_combo, self.speed_spin, self.resolution_combo, self.quality_spin,
            self.fps_check, self.bitrate_check
        ]
        
        for widget in controls_to_toggle:
            widget.setEnabled(enable_adv)
            
        # FPS 和 碼率 SpinBox 還有額外的 CheckBox 連動邏輯
        if enable_adv:
            self.fps_spin.setEnabled(self.fps_check.isChecked())
            self.bitrate_spin.setEnabled(self.bitrate_check.isChecked())
        else:
            self.fps_spin.setEnabled(False)
            self.bitrate_spin.setEnabled(False)
            
        self.estimate_file_size()

    def select_video(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "選擇影片檔案", "", 
            "影片檔案 (*.mp4 *.mkv *.avi *.mov *.webm *.flv *.wmv *.m4v *.mpg *.mpeg);;所有檔案 (*.*)"
        )
        if file_path:
            self.load_video(file_path)
    
    def load_video(self, file_path):
        self.video_path = file_path
        self.file_text_label.setText(os.path.basename(file_path))
        self.file_text_label.setStyleSheet("color: #14a085; font-weight: bold; font-size: 14px;")
        
        self.media_player.setSource(QUrl.fromLocalFile(file_path))
        self.media_player.pause()
        self.media_player.setPosition(0)
        
        self.play_btn.setEnabled(True)
        self.step_back_btn.setEnabled(True)
        self.step_fwd_btn.setEnabled(True)
        self.position_slider.setEnabled(True)
        self.start_slider.setEnabled(True)
        self.end_slider.setEnabled(True)
        self.process_btn.setEnabled(True)
        self.preview_speed_check.setEnabled(True)
        self.jump_start_btn.setEnabled(True)
        self.jump_end_btn.setEnabled(True)
        self.start_back_btn.setEnabled(True)
        self.start_fwd_btn.setEnabled(True)
        self.end_back_btn.setEnabled(True)
        self.end_fwd_btn.setEnabled(True)
        
        self.preview_speed_check.setChecked(False)
        self.media_player.setPlaybackRate(1.0)
        self.get_video_info()
    
    def get_video_info(self):
        try:
            ffprobe_path = get_tool_path("ffprobe.exe")
            cmd = [ffprobe_path, '-v', 'quiet', '-print_format', 'json',
                   '-show_format', '-show_streams', self.video_path]
            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', startupinfo=startupinfo)
            info = json.loads(result.stdout)
            
            duration = float(info['format']['duration'])
            self.video_duration = duration
            
            if 'bit_rate' in info['format']:
                self.video_bitrate = int(info['format']['bit_rate']) / 1000 
            
            video_stream = next((s for s in info['streams'] if s['codec_type'] == 'video'), None)
            if video_stream:
                width = video_stream.get('width', 'N/A')
                height = video_stream.get('height', 'N/A')
                fps = eval(video_stream.get('r_frame_rate', '0/1'))
                
                # 更新資訊卡片
                self.lbl_res_val.setText(f"{width}x{height}")
                self.lbl_fps_val.setText(f"{fps:.2f}")
                self.lbl_bitrate_val.setText(f"{self.video_bitrate:.0f} kbps")
                self.lbl_dur_val.setText(self.format_time(duration))
                self.file_info_box.setVisible(True) 
                
                self.info_label.setVisible(False)
            
            max_ms = int(duration * 1000)
            self.position_slider.setMaximum(max_ms)
            self.start_slider.setMaximum(max_ms)
            self.end_slider.setMaximum(max_ms)
            self.end_slider.setValue(max_ms)
            self.update_range_labels()
            self.estimate_file_size()
            
        except Exception as e:
            QMessageBox.warning(self, "警告", f"無法讀取影片資訊: {str(e)}")
    
    def toggle_play(self):
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
            self.play_btn.setText("▶ 播放")
        else:
            self.media_player.play()
            self.play_btn.setText("⏸ 暫停")
            self.toggle_preview_speed()
    
    def step_video(self, direction):
        if self.media_player.mediaStatus() == QMediaPlayer.MediaStatus.NoMedia:
            return
        step_text = self.step_combo.currentText()
        step_seconds = float(step_text.split()[0])
        step_ms = int(step_seconds * 1000)
        current_pos = self.media_player.position()
        if direction == 'fwd':
            new_pos = min(current_pos + step_ms, self.video_duration * 1000)
        else:
            new_pos = max(current_pos - step_ms, 0)
        self.media_player.setPosition(int(new_pos))
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
            self.play_btn.setText("▶ 播放")
    
    def adjust_range_time(self, target, direction):
        slider = self.start_slider if target == 'start' else self.end_slider
        
        step_text = self.step_combo.currentText()
        step_seconds = float(step_text.split()[0])
        step_ms = int(step_seconds * 1000)
        
        current_val = slider.value()
        if direction == 'fwd':
            new_val = min(current_val + step_ms, self.video_duration * 1000)
        else:
            new_val = max(current_val - step_ms, 0)
        
        slider.setValue(int(new_val))
        self.seek_to_range(target)

    def toggle_preview_speed(self):
        if self.preview_speed_check.isChecked():
            self.media_player.setPlaybackRate(2.0)
        else:
            self.media_player.setPlaybackRate(1.0)

    def update_position(self, position):
        if not self.position_slider.isSliderDown():
            self.position_slider.setValue(position)
        self.current_time_label.setText(self.format_time(position / 1000, show_ms=True))
    
    def update_duration(self, duration):
        self.position_slider.setMaximum(duration)
        self.duration_label.setText(self.format_time(duration / 1000))
    
    def set_position(self, position):
        self.media_player.setPosition(position)
    
    def set_from_current(self, target):
        current_pos = self.media_player.position()
        if target == 'start':
            self.start_slider.setValue(current_pos)
        else:
            self.end_slider.setValue(current_pos)
            
    def seek_to_range(self, point):
        val = (self.start_slider if point == 'start' else self.end_slider).value()
        self.media_player.setPosition(val)
        self.position_slider.setValue(val)
        self.current_time_label.setText(self.format_time(val / 1000, True))
    
    def update_range_labels(self):
        start = self.start_slider.value() / 1000
        end = self.end_slider.value() / 1000
        self.start_time_label.setText(self.format_time(start))
        self.end_time_label.setText(self.format_time(end))
        duration = max(0, end - start)
        self.range_info_label.setText(f"剪輯長度: {self.format_time(duration)}")
    
    def format_time(self, seconds, show_ms=False):
        t = timedelta(seconds=int(seconds))
        if show_ms:
            ms = int((seconds - int(seconds)) * 1000)
            return f"{str(t)}.{ms:03d}"
        return str(t)
    
    def estimate_file_size(self):
        if not self.video_path or self.video_bitrate == 0:
            return
        start = self.start_slider.value() / 1000
        end = self.end_slider.value() / 1000
        speed = 1.0
        if self.compress_mode_radio.isChecked():
            speed = self.speed_spin.value()
        duration = max(0, (end - start) / speed)
        if duration == 0:
            self.size_estimate_label.setText("預估大小: 0 MB")
            return
        
        if self.copy_mode_radio.isChecked():
            size_mb = (self.video_bitrate * duration) / 8 / 1024
            self.size_estimate_label.setText(f"預估大小: {size_mb:.1f} MB (無損)")
        else:
            if self.bitrate_check.isChecked():
                bitrate = self.bitrate_spin.value()
            else:
                crf = self.quality_spin.value()
                bitrate = self.video_bitrate * (1 - (crf / 51) * 0.7)
            
            resolution_text = self.resolution_combo.currentText()
            if '720' in resolution_text:
                bitrate *= 0.4
            elif '1080' in resolution_text:
                bitrate *= 0.6
            elif '2K' in resolution_text:
                bitrate *= 0.8
            
            size_mb = (bitrate * duration) / 8 / 1024
            self.size_estimate_label.setText(f"預估大小: {size_mb:.1f} MB (壓縮)")
    
    def process_video(self):
        if not self.video_path:
            return
        start_time = self.start_slider.value() / 1000
        end_time = self.end_slider.value() / 1000
        if start_time >= end_time:
            QMessageBox.warning(self, "錯誤", "開始時間必須小於結束時間！")
            return
        
        output_format = self.format_combo.currentText()
        default_name = f"{os.path.splitext(os.path.basename(self.video_path))[0]}_cut.{output_format}"
        output_path, _ = QFileDialog.getSaveFileName(self, "儲存影片", default_name, f"{output_format.upper()} 檔案 (*.{output_format})")
        if not output_path:
            return
        
        mode = 'copy' if self.copy_mode_radio.isChecked() else 'compress'
        quality = self.quality_spin.value() if mode == 'compress' else None
        fps = self.fps_spin.value() if mode == 'compress' and self.fps_check.isChecked() else None
        bitrate = self.bitrate_spin.value() if mode == 'compress' and self.bitrate_check.isChecked() else None
        speed = self.speed_spin.value() if mode == 'compress' else 1.0 
        
        resolution = None
        if mode == 'compress':
            res_text = self.resolution_combo.currentText()
            if '原始' not in res_text:
                resolution = '-1:-1' 
                if '4K' in res_text: resolution = '3840:-1'
                if '2K' in res_text: resolution = '2560:-1'
                if '1080p' in res_text: resolution = '1920:-1'
                if '720p' in res_text: resolution = '1280:-1'
        
        gpu_vendor = 'CPU'
        if mode == 'compress':
            gpu_vendor = self.gpu_combo.currentText()
        
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.process_btn.setEnabled(False)
        
        self.processor = VideoProcessor(
            self.video_path, output_path, start_time, end_time, 
            mode, quality, fps, bitrate, output_format, resolution, 
            gpu_vendor, speed
        )
        self.processor.progress.connect(self.progress_bar.setValue)
        self.processor.finished.connect(self.process_finished)
        self.processor.start()
    
    def process_finished(self, success, message):
        self.progress_bar.setVisible(False)
        self.process_btn.setEnabled(True)
        if success:
            QMessageBox.information(self, "完成", message)
        else:
            QMessageBox.critical(self, "錯誤", message)

def main():
    if hasattr(Qt.HighDpiScaleFactorRoundingPolicy, 'PassThrough'):
        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    window = VideoCutter()
    window.show()
    sys.exit(app.exec())

if __name__ == '__main__':
    main()