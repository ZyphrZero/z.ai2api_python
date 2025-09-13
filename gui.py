#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
GUI application for controlling the Z.AI API Server
"""

import tkinter as tk
from tkinter import ttk
import subprocess
import sys
import os
import threading
import queue
import winreg
import ctypes
from collections import OrderedDict
import win32api
import win32gui
import win32con

class WindowsTrayIcon:
    """
    A class to manage a system tray icon using pywin32.
    This runs in a separate thread to not block the main GUI.
    """
    def __init__(self, class_name, title, callbacks):
        self.class_name = class_name
        self.title = title
        self.callbacks = callbacks
        self.hwnd = None
        self.message_map = {
            win32con.WM_DESTROY: self.on_destroy,
            win32con.WM_USER + 20: self.on_tray_event,
            win32con.WM_COMMAND: self.on_command,
        }

    def run(self):
        """Register window class, create window, and start message pump."""
        wc = win32gui.WNDCLASS()
        hinst = wc.hInstance = win32api.GetModuleHandle(None)
        wc.lpszClassName = self.class_name
        wc.lpfnWndProc = self.message_map
        class_atom = win32gui.RegisterClass(wc)

        style = win32con.WS_OVERLAPPED | win32con.WS_SYSMENU
        self.hwnd = win32gui.CreateWindow(
            class_atom, self.class_name, style, 0, 0,
            win32con.CW_USEDEFAULT, win32con.CW_USEDEFAULT,
            0, 0, hinst, None
        )
        win32gui.UpdateWindow(self.hwnd)
        self._create_icon()
        win32gui.PumpMessages()

    def _create_icon(self):
        """Add the icon to the system tray."""
        hinst = win32api.GetModuleHandle(None)
        try:
            hicon = win32gui.LoadIcon(0, win32con.IDI_APPLICATION)
        except Exception:
            hicon = win32gui.LoadIcon(0, win32con.IDI_ERROR)

        flags = win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP
        nid = (self.hwnd, 0, flags, win32con.WM_USER + 20, hicon, self.title)
        win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, nid)

    def on_destroy(self, hwnd, msg, wparam, lparam):
        """Clean up when the window is destroyed."""
        nid = (self.hwnd, 0)
        win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, nid)
        win32gui.PostQuitMessage(0)

    def on_tray_event(self, hwnd, msg, wparam, lparam):
        """Handle events from the tray icon (clicks)."""
        if lparam == win32con.WM_LBUTTONDBLCLK:
            self.callbacks.get('show', lambda: None)()
        elif lparam == win32con.WM_RBUTTONUP:
            self._show_menu()
        return 1

    def on_command(self, hwnd, msg, wparam, lparam):
        """Handle menu item clicks."""
        command_id = win32api.LOWORD(wparam)
        if command_id == 1001:
            self.callbacks.get('show', lambda: None)()
        elif command_id == 1002:
            self.callbacks.get('quit', lambda: None)()

    def _show_menu(self):
        """Create and display the right-click context menu."""
        menu = win32gui.CreatePopupMenu()
        win32gui.AppendMenu(menu, win32con.MF_STRING, 1001, "显示")
        win32gui.AppendMenu(menu, win32con.MF_STRING, 1002, "退出")
        
        pos = win32gui.GetCursorPos()
        win32gui.SetForegroundWindow(self.hwnd)
        win32gui.TrackPopupMenu(menu, win32con.TPM_LEFTALIGN, pos[0], pos[1], 0, self.hwnd, None)
        win32gui.PostMessage(self.hwnd, win32con.WM_NULL, 0, 0)

    def destroy(self):
        """Destroy the tray icon window."""
        if self.hwnd:
            win32gui.PostMessage(self.hwnd, win32con.WM_CLOSE, 0, 0)


class ApiServerGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Z.AI API Server 控制台")
        self.root.geometry("850x650")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.server_process = None
        self.is_server_running = False
        self.tray_icon = None
        self.update_id = None
        
        self._define_env_vars()
        self.config_vars = {}

        self.create_widgets()
        self.update_status()
        self.check_startup_status()
        self.load_settings()
        self.setup_tray_icon()

    def _define_env_vars(self):
        """Define metadata for each environment variable to build the UI."""
        self.env_definitions = OrderedDict([
            ('API_ENDPOINT', {'desc': 'Z.ai API 端点地址', 'type': 'entry'}),
            ('AUTH_TOKEN', {'desc': '客户端认证密钥', 'type': 'entry'}),
            ('SKIP_AUTH_TOKEN', {'desc': '跳过客户端认证 (开发用)', 'type': 'bool'}),
            ('BACKUP_TOKEN', {'desc': 'Z.ai 备用访问令牌', 'type': 'entry'}),
            ('LISTEN_PORT', {'desc': '服务监听端口', 'type': 'entry'}),
            ('DEBUG_LOGGING', {'desc': '启用调试日志', 'type': 'bool'}),
            ('THINKING_PROCESSING', {'desc': '思考内容处理策略', 'type': 'combo', 'options': ['think', 'strip', 'raw']}),
            ('ANONYMOUS_MODE', {'desc': '启用匿名模式 (推荐)', 'type': 'bool'}),
            ('TOOL_SUPPORT', {'desc': '启用 Function Call 功能', 'type': 'bool'}),
            ('SCAN_LIMIT', {'desc': '工具调用扫描限制 (字符)', 'type': 'entry'}),
        ])

    def create_widgets(self):
        """Create the main window widgets using a tabbed layout."""
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        main_tab = ttk.Frame(notebook)
        settings_tab = ttk.Frame(notebook)

        notebook.add(main_tab, text='主控')
        notebook.add(settings_tab, text='设置')

        self._create_main_tab(main_tab)
        self._create_settings_tab(settings_tab)

    def _create_main_tab(self, parent):
        """Create widgets for the main control tab."""
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        # --- Top control frame ---
        control_frame = ttk.Frame(parent, padding=(0, 10))
        control_frame.grid(row=0, column=0, sticky="ew")
        
        self.status_label = ttk.Label(control_frame, text="服务状态: 未运行", font=("Helvetica", 12))
        self.status_label.pack(side=tk.LEFT, padx=(0, 15))

        self.start_button = ttk.Button(control_frame, text="启动服务", command=self.start_server)
        self.start_button.pack(side=tk.LEFT, padx=5)

        self.stop_button = ttk.Button(control_frame, text="停止服务", command=self.stop_server, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=5)

        # --- Log area ---
        log_frame = ttk.LabelFrame(parent, text="服务日志")
        log_frame.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.log_text = tk.Text(log_frame, state=tk.DISABLED, wrap=tk.WORD)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        
        log_scrollbar = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        log_scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.config(yscrollcommand=log_scrollbar.set)

    def _create_settings_tab(self, parent):
        """Create widgets for the settings tab."""
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        # Canvas and Scrollbar for a scrollable area
        canvas = tk.Canvas(parent, highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        # --- Populate settings widgets ---
        self._populate_settings_widgets(scrollable_frame)

        # --- Bottom action buttons ---
        action_frame = ttk.Frame(parent, padding=(0, 10))
        action_frame.grid(row=1, column=0, columnspan=2, sticky="ew")

        self.save_settings_button = ttk.Button(action_frame, text="保存设置", command=self.save_settings)
        self.save_settings_button.pack(side=tk.RIGHT)
        
        self.startup_var = tk.BooleanVar()
        self.startup_checkbox = ttk.Checkbutton(
            action_frame,
            text="开机自启动",
            variable=self.startup_var,
            command=self.toggle_startup
        )
        self.startup_checkbox.pack(side=tk.LEFT)

    def _populate_settings_widgets(self, parent):
        """Dynamically create widgets for each setting."""
        for i, (key, definition) in enumerate(self.env_definitions.items()):
            # Create a variable for the widget
            if definition['type'] == 'bool':
                var = tk.BooleanVar()
            else:
                var = tk.StringVar()
            self.config_vars[key] = var

            # Create label
            label = ttk.Label(parent, text=definition['desc'])
            label.grid(row=i, column=0, sticky="w", padx=10, pady=5)

            # Create widget based on type
            if definition['type'] == 'entry':
                widget = ttk.Entry(parent, textvariable=var, width=50)
                widget.grid(row=i, column=1, sticky="ew", padx=10, pady=5)
            elif definition['type'] == 'bool':
                widget = ttk.Checkbutton(parent, variable=var)
                widget.grid(row=i, column=1, sticky="w", padx=10, pady=5)
            elif definition['type'] == 'combo':
                widget = ttk.Combobox(parent, textvariable=var, values=definition['options'], state='readonly')
                widget.grid(row=i, column=1, sticky="w", padx=10, pady=5)
        parent.columnconfigure(1, weight=1)

    def log_message(self, message):
        """Add a message to the log text widget"""
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def get_env_path(self):
        """Get the path to the .env file."""
        if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
            return os.path.join(os.path.dirname(sys.executable), '.env')
        else:
            return os.path.join(os.path.dirname(__file__), '.env')

    def load_settings(self):
        """Load settings from .env file and populate the UI widgets."""
        env_path = self.get_env_path()
        current_values = {}
        try:
            with open(env_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        current_values[key.strip()] = value.strip()
            self.log_message(f"已加载配置文件: {env_path}")
        except FileNotFoundError:
            self.log_message("警告: 未找到 .env 文件，将使用默认值。")
        except Exception as e:
            self.log_message(f"加载 .env 文件时出错: {e}")

        # Set widget values
        for key, var in self.config_vars.items():
            value = current_values.get(key, '')
            if isinstance(var, tk.BooleanVar):
                var.set(value.lower() == 'true')
            else:
                var.set(value)

    def save_settings(self):
        """Save settings from UI widgets back to the .env file."""
        env_path = self.get_env_path()
        try:
            # Read existing file to preserve comments and structure
            if os.path.exists(env_path):
                with open(env_path, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
            else:
                lines = []

            new_lines = []
            processed_keys = set()

            # Update existing keys
            for line in lines:
                stripped_line = line.strip()
                if stripped_line and not stripped_line.startswith('#') and '=' in stripped_line:
                    key = stripped_line.split('=', 1)[0].strip()
                    if key in self.config_vars:
                        var = self.config_vars[key]
                        if isinstance(var, tk.BooleanVar):
                            value = 'true' if var.get() else 'false'
                        else:
                            value = var.get()
                        new_lines.append(f"{key}={value}\n")
                        processed_keys.add(key)
                    else:
                        new_lines.append(line) # Preserve unknown keys
                else:
                    new_lines.append(line) # Preserve comments and empty lines

            # Add any new keys that weren't in the original file
            for key, var in self.config_vars.items():
                if key not in processed_keys:
                    if isinstance(var, tk.BooleanVar):
                        value = 'true' if var.get() else 'false'
                    else:
                        value = var.get()
                    new_lines.append(f"{key}={value}\n")

            with open(env_path, 'w', encoding='utf-8') as f:
                f.writelines(new_lines)

            self.log_message(f"配置文件已保存: {env_path}")
            if self.is_server_running:
                self.log_message("请重启服务以应用新的配置。")
        except Exception as e:
            self.log_message(f"保存 .env 文件时出错: {e}")

    def update_status(self):
        """Update the status label and button states"""
        if self.is_server_running:
            self.status_label.config(text="服务状态: 运行中", foreground="green")
            self.start_button.config(state=tk.DISABLED)
            self.stop_button.config(state=tk.NORMAL)
        else:
            self.status_label.config(text="服务状态: 未运行", foreground="red")
            self.start_button.config(state=tk.NORMAL)
            self.stop_button.config(state=tk.DISABLED)
        self.update_id = self.root.after(1000, self.update_status) # Check every second

    def start_server(self):
        """Start the API server in a background thread"""
        if not self.is_server_running:
            self.log_message("正在启动服务...")
            # Determine the path to main.py
            if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
                application_path = os.path.join(sys._MEIPASS, 'main.py')
            else:
                application_path = os.path.join(os.path.dirname(__file__), 'main.py')
            
            python_executable = sys.executable
            
            self.server_process = subprocess.Popen(
                [python_executable, application_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            self.is_server_running = True
            self.log_message(f"服务已启动 (PID: {self.server_process.pid})")
            threading.Thread(target=self.read_server_output, daemon=True).start()

    def read_server_output(self):
        """Read stdout from the server process and log it"""
        if self.server_process:
            for line in iter(self.server_process.stdout.readline, ''):
                self.log_message(f"[SERVER] {line.strip()}")
            self.server_process.stdout.close()
            return_code = self.server_process.wait()
            self.is_server_running = False
            self.log_message(f"服务已停止 (返回码: {return_code})")

    def stop_server(self):
        """Stop the API server"""
        if self.is_server_running and self.server_process:
            self.log_message("正在停止服务...")
            self.server_process.terminate()
            try:
                self.server_process.wait(timeout=5)
                self.log_message("服务已成功停止。")
            except subprocess.TimeoutExpired:
                self.log_message("服务停止超时，正在强制终止...")
                self.server_process.kill()
                self.log_message("服务已被强制终止。")
            self.is_server_running = False
            self.server_process = None

    def on_close(self):
        """Handle window close event by hiding the window."""
        self.hide_window()

    def setup_tray_icon(self):
        """Create the system tray icon using pywin32."""
        callbacks = {
            'show': self.show_window,
            'quit': self.quit_app,
        }
        self.tray_icon = WindowsTrayIcon(
            "Z_AI_API_TRAY_CLASS",
            "Z.AI API Server",
            callbacks
        )
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def show_window(self):
        """Show the main window."""
        self.root.after(0, self.root.deiconify)

    def hide_window(self):
        """Hide the main window."""
        self.root.withdraw()

    def quit_app(self):
        """Quit the application."""
        # Cancel the recurring update to prevent errors on exit
        if self.update_id:
            self.root.after_cancel(self.update_id)
            self.update_id = None
            
        self.stop_server()
        if self.tray_icon:
            self.tray_icon.destroy()
        self.root.destroy()

    def get_startup_key_path(self):
        """Get the registry key path for startup"""
        return winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_ALL_ACCESS
        )

    def check_startup_status(self):
        """Check if the application is set to run on startup"""
        try:
            key = self.get_startup_key_path()
            app_name = "Z.AI API Server"
            try:
                winreg.QueryValueEx(key, app_name)
                self.startup_var.set(True)
            except FileNotFoundError:
                self.startup_var.set(False)
            winreg.CloseKey(key)
        except WindowsError:
            self.log_message("无法访问注册表以检查启动状态。")
            self.startup_checkbox.config(state=tk.DISABLED)

    def toggle_startup(self):
        """Toggle the application's startup setting"""
        try:
            key = self.get_startup_key_path()
            app_name = "Z.AI API Server"
            
            if getattr(sys, 'frozen', False):
                app_path = f'"{sys.executable}" --hidden'
            else:
                python_executable = sys.executable
                gui_script_path = os.path.abspath(__file__)
                app_path = f'"{python_executable}" "{gui_script_path}" --hidden'

            if self.startup_var.get():
                winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, app_path)
                self.log_message(f"已添加到开机自启动: {app_path}")
            else:
                try:
                    winreg.DeleteValue(key, app_name)
                    self.log_message("已从开机自启动中移除。")
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
        except WindowsError as e:
            self.log_message(f"无法修改启动项: {e}")
            self.startup_var.set(not self.startup_var.get())

def main():
    """Main entry point for the GUI application"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass

    hidden_mode = "--hidden" in sys.argv

    root = tk.Tk()
    app = ApiServerGUI(root)

    if hidden_mode:
        app.hide_window()

    try:
        root.mainloop()
    except KeyboardInterrupt:
        app.quit_app()

if __name__ == "__main__":
    main()