#!/usr/bin/env python3
"""
嘉立创泰山派 RTK3566 - USB三麦克风录音上传系统
录制完成后自动上传至服务器并删除本地文件
"""

import subprocess
import time
import os
import signal
import sys
import threading
from datetime import datetime
from queue import Queue
import re
import json
import socket

class RecordUploader:
    def __init__(self, server_ip, server_port, output_dir="usb_recordings", duration=60, interval=1):
        self.output_dir = output_dir
        self.duration = duration
        self.interval = interval
        self.server_ip = server_ip
        self.server_port = server_port
        self.is_running = True
        self.recording_processes = {}
        self.mic_devices = {}
        self.record_count = {"M1": 0, "M2": 0, "M3": 0}
        self.upload_count = {"M1": 0, "M2": 0, "M3": 0}
        self.fail_count = {"M1": 0, "M2": 0, "M3": 0}
        self.consecutive_fails = {"M1": 0, "M2": 0, "M3": 0}
        self.lock = threading.Lock()
        
        self.usb_config_file = os.path.join(output_dir, "usb_mic_config.json")
        self.device_retry_count = 3
        self.device_init_delay = 0.5
        
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        
        for i in range(1, 4):
            mic_dir = os.path.join(output_dir, f"M{i}")
            if not os.path.exists(mic_dir):
                os.makedirs(mic_dir)
        
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
    
    def signal_handler(self, signum, frame):
        print("\n\n收到停止信号，正在安全退出...")
        self.is_running = False
        self.stop_all_recordings()
        self.save_device_config()
        sys.exit(0)
    
    def save_device_config(self):
        try:
            config = {}
            for mic_id, info in self.mic_devices.items():
                config[mic_id] = {
                    'device': info['device'],
                    'usb_path': info.get('usb_path', ''),
                    'serial': info.get('serial', ''),
                    'last_success': info.get('last_success', 0)
                }
            
            with open(self.usb_config_file, 'w') as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            print(f"⚠ 配置保存失败: {e}")
    
    def load_device_config(self):
        try:
            if os.path.exists(self.usb_config_file):
                with open(self.usb_config_file, 'r') as f:
                    config = json.load(f)
                return config
        except Exception as e:
            print(f"⚠ 配置加载失败: {e}")
        return None
    
    def test_device_quick(self, device, duration=1):
        try:
            test_file = f"/tmp/usb_mic_test_{os.getpid()}.wav"
            result = subprocess.run([
                "arecord", "-D", device,
                "-f", "S16_LE", "-r", "16000", "-c", "1",
                "-d", str(duration), "-q", test_file
            ], capture_output=True, timeout=duration + 3)
            
            success = False
            if os.path.exists(test_file):
                if os.path.getsize(test_file) > 500:
                    success = True
                os.remove(test_file)
            
            return success
        except:
            return False
    
    def detect_usb_mics_stable(self):
        print("=" * 60)
        print("稳定检测USB麦克风设备...")
        print("=" * 60)
        
        usb_devices_found = []
        
        print("\n[1] ALSA设备检测:")
        try:
            result = subprocess.run(
                ["arecord", "-l"], 
                capture_output=True, 
                text=True
            )
            
            for line in result.stdout.split('\n'):
                if any(keyword in line.lower() for keyword in ['rockchip', 'rk809', 'rk817', 'hdmi']):
                    continue
                
                if 'card' in line and ':' in line and 'device' in line:
                    match = re.search(r'card (\d+):.*device (\d+):', line)
                    if match:
                        card_num = match.group(1)
                        device_num = match.group(2)
                        plughw_id = f"plughw:{card_num},{device_num}"
                        
                        name_match = re.search(r'\[(.+?)\]', line)
                        device_name = name_match.group(1) if name_match else f"设备{card_num}:{device_num}"
                        
                        if self.test_device_quick(plughw_id):
                            usb_devices_found.append({
                                'card': card_num,
                                'device': device_num,
                                'plughw_id': plughw_id,
                                'name': device_name,
                                'type': 'USB'
                            })
        except Exception as e:
            print(f"  ✗ ALSA检测失败: {e}")
        
        print("\n[2] PCM设备枚举:")
        try:
            result = subprocess.run(
                ["arecord", "-L"], 
                capture_output=True, 
                text=True
            )
            
            existing_devices = [d['plughw_id'] for d in usb_devices_found]
            
            for line in result.stdout.split('\n'):
                line = line.strip()
                if not line or line.startswith(' ') or 'CARD=' not in line:
                    continue
                
                if any(keyword in line.lower() for keyword in ['rockchip', 'rk809', 'rk817']):
                    continue
                
                if line not in existing_devices and 'plughw:' in line:
                    if self.test_device_quick(line):
                        match = re.search(r'plughw:(\d+),(\d+)', line)
                        if match:
                            card_num = match.group(1)
                            device_num = match.group(2)
                            usb_devices_found.append({
                                'card': card_num,
                                'device': device_num,
                                'plughw_id': line,
                                'name': line,
                                'type': 'USB'
                            })
                            existing_devices.append(line)
        except Exception as e:
            print(f"  ✗ PCM枚举失败: {e}")
        
        seen = set()
        unique_devices = []
        for dev in usb_devices_found:
            key = dev['plughw_id']
            if key not in seen:
                seen.add(key)
                unique_devices.append(dev)
        
        print(f"\n  总计找到 {len(unique_devices)} 个可用USB音频设备")
        return unique_devices
    
    def assign_mic_devices_stable(self, usb_devices):
        print("\n" + "=" * 60)
        print("稳定分配USB麦克风设备...")
        print("=" * 60)
        
        previous_config = self.load_device_config()
        self.mic_devices = {}
        mic_labels = ['M1', 'M2', 'M3']
        
        if len(usb_devices) >= 3:
            print("✓ 检测到3个或更多USB设备")
            
            if previous_config:
                print("  使用历史成功配置...")
                for i, label in enumerate(mic_labels):
                    if label in previous_config and i < len(usb_devices):
                        prev_device = previous_config[label]['device']
                        if self.test_device_quick(prev_device):
                            self.mic_devices[label] = {
                                'device': prev_device,
                                'type': 'USB',
                                'description': f'USB麦克风{i+1}(历史)',
                                'last_success': time.time()
                            }
                            continue
                
                used_devices = [v['device'] for v in self.mic_devices.values()]
                available = [d for d in usb_devices if d['plughw_id'] not in used_devices]
                
                for i, label in enumerate(mic_labels):
                    if label not in self.mic_devices and i < len(available):
                        dev = available[i]
                        self.mic_devices[label] = {
                            'device': dev['plughw_id'],
                            'type': 'USB',
                            'description': f'USB麦克风(新)',
                            'last_success': time.time()
                        }
            else:
                for i, label in enumerate(mic_labels):
                    dev = usb_devices[i]
                    self.mic_devices[label] = {
                        'device': dev['plughw_id'],
                        'type': 'USB',
                        'description': f'USB麦克风{i+1}',
                        'last_success': time.time()
                    }
        
        elif len(usb_devices) == 2:
            print("⚠ 检测到2个USB设备")
            for i in range(2):
                dev = usb_devices[i]
                self.mic_devices[mic_labels[i]] = {
                    'device': dev['plughw_id'],
                    'type': 'USB',
                    'description': f'USB麦克风{i+1}',
                    'last_success': time.time()
                }
        
        elif len(usb_devices) == 1:
            print("⚠ 仅检测到1个USB设备")
            dev = usb_devices[0]
            self.mic_devices[mic_labels[0]] = {
                'device': dev['plughw_id'],
                'type': 'USB',
                'description': 'USB麦克风1',
                'last_success': time.time()
            }
        else:
            print("✗ 未检测到USB麦克风设备")
            return False
        
        self.save_device_config()
        
        print("\n最终设备分配:")
        for mic_id in sorted(self.mic_devices.keys()):
            info = self.mic_devices[mic_id]
            print(f"  [{mic_id}] {info['description']}: {info['device']}")
        
        print(f"\n  可用设备数: {len(self.mic_devices)}/3")
        return len(self.mic_devices) > 0
    
    def upload_file(self, file_path, mic_id):
        """上传单个文件到服务器"""
        if not os.path.exists(file_path):
            print(f"    [{mic_id}] 文件不存在: {file_path}")
            return False
        
        file_name = os.path.basename(file_path)
        file_size = os.path.getsize(file_path)
        
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.connect((self.server_ip, self.server_port))
                
                header = f"{mic_id}|{file_name}|{file_size}"
                sock.sendall(header.encode('utf-8'))
                
                response = sock.recv(1024).decode('utf-8')
                if response != 'READY':
                    print(f"    [{mic_id}] 服务器拒绝: {response}")
                    return False
                
                with open(file_path, 'rb') as f:
                    bytes_sent = 0
                    while bytes_sent < file_size:
                        chunk = f.read(4096)
                        if not chunk:
                            break
                        sock.sendall(chunk)
                        bytes_sent += len(chunk)
                
                confirm = sock.recv(1024).decode('utf-8')
                if confirm == 'OK':
                    print(f"    [{mic_id}] 上传成功")
                    return True
                else:
                    print(f"    [{mic_id}] 上传确认失败")
                    return False
                    
        except ConnectionRefusedError:
            print(f"    [{mic_id}] 无法连接到服务器")
            return False
        except Exception as e:
            print(f"    [{mic_id}] 上传失败: {e}")
            return False
    
    def record_and_upload(self, mic_id, device, timestamp):
        """录音并上传"""
        filename = os.path.join(self.output_dir, mic_id, f"{timestamp}_{mic_id}.wav")
        
        for attempt in range(1, self.device_retry_count + 1):
            try:
                if attempt > 1:
                    time.sleep(self.device_init_delay * attempt)
                
                cmd = [
                    "arecord",
                    "-D", device,
                    "-f", "S16_LE",
                    "-r", "16000",
                    "-c", "1",
                    "-d", str(self.duration),
                    "--buffer-size=4096",
                    "--period-size=1024",
                    "--nonblock",
                    filename
                ]
                
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    bufsize=4096
                )
                
                with self.lock:
                    self.recording_processes[mic_id] = process
                
                try:
                    process.wait(timeout=self.duration + 5)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=3)
                    raise
                
                with self.lock:
                    self.recording_processes.pop(mic_id, None)
                
                if os.path.exists(filename):
                    file_size = os.path.getsize(filename) / 1024
                    expected_size_min = (self.duration * 16000 * 2) / 1024 * 0.5
                    expected_size_max = (self.duration * 16000 * 2) / 1024 * 1.2
                    
                    if file_size >= expected_size_min and file_size <= expected_size_max:
                        print(f"  ✓ [{mic_id}] 录音完成: {file_size:.1f} KB")
                        
                        print(f"  -> [{mic_id}] 正在上传...")
                        if self.upload_file(filename, mic_id):
                            with self.lock:
                                self.record_count[mic_id] += 1
                                self.upload_count[mic_id] += 1
                                self.consecutive_fails[mic_id] = 0
                            
                            os.remove(filename)
                            print(f"  -> [{mic_id}] 已删除本地文件")
                            return True
                        else:
                            print(f"  ✗ [{mic_id}] 上传失败，保留本地文件")
                            return False
                    else:
                        if attempt == self.device_retry_count:
                            print(f"  ✗ [{mic_id}] 文件大小异常")
                        try:
                            os.remove(filename)
                        except:
                            pass
                else:
                    if attempt == self.device_retry_count:
                        print(f"  ✗ [{mic_id}] 录音文件未创建")
            
            except Exception as e:
                if attempt == self.device_retry_count:
                    print(f"  ✗ [{mic_id}] 录音异常: {e}")
        
        with self.lock:
            self.fail_count[mic_id] += 1
            self.consecutive_fails[mic_id] += 1
            
            if self.consecutive_fails[mic_id] >= 5:
                print(f"  ⚠ [{mic_id}] 连续失败5次，尝试切换设备...")
        
        return False
    
    def stop_all_recordings(self):
        with self.lock:
            for mic_id, process in list(self.recording_processes.items()):
                try:
                    if process.poll() is None:
                        process.terminate()
                        process.wait(timeout=3)
                except:
                    try:
                        process.kill()
                    except:
                        pass
    
    def show_status(self):
        with self.lock:
            total_record = sum(self.record_count.values())
            total_upload = sum(self.upload_count.values())
            total_fail = sum(self.fail_count.values())
            
            print(f"\n{'='*60}")
            print(f"录音上传状态")
            print(f"{'='*60}")
            
            for mic_id in ['M1', 'M2', 'M3']:
                record = self.record_count[mic_id]
                upload = self.upload_count[mic_id]
                fails = self.fail_count[mic_id]
                consecutive = self.consecutive_fails[mic_id]
                
                status = "✓" if consecutive == 0 else "⚠" if consecutive < 3 else "✗"
                print(f"  {status} {mic_id}: 录音{record} | 上传{upload} | 失败{fails}")
            
            print(f"  总计: 录音{total_record} | 上传{total_upload} | 失败{total_fail}")
            print(f"{'='*60}\n")
    
    def run(self):
        print("\n" + "=" * 60)
        print("  泰山派USB三麦克风录音上传系统")
        print("=" * 60)
        print(f"  录音时长: {self.duration}秒")
        print(f"  录音间隔: {self.interval}秒")
        print(f"  服务器地址: {self.server_ip}:{self.server_port}")
        print(f"  音频格式: 16kHz, 16bit, Mono, WAV")
        print("  按 Ctrl+C 停止程序")
        print("=" * 60 + "\n")
        
        usb_devices = self.detect_usb_mics_stable()
        
        if not usb_devices:
            print("\n✗✗✗ 未检测到任何USB麦克风设备")
            return
        
        if not self.assign_mic_devices_stable(usb_devices):
            print("\n✗ 无法分配足够的USB麦克风设备")
            return
        
        print("\n开始录音上传...\n")
        
        try:
            while self.is_running:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 开始新一组录音 ({timestamp})")
                
                threads = []
                
                for mic_id, info in self.mic_devices.items():
                    thread = threading.Thread(
                        target=self.record_and_upload,
                        args=(mic_id, info['device'], timestamp),
                        daemon=True
                    )
                    thread.start()
                    threads.append((mic_id, thread))
                    time.sleep(self.device_init_delay)
                
                for mic_id, thread in threads:
                    thread.join(timeout=self.duration + 20)
                
                if sum(self.record_count.values()) % 5 == 0:
                    self.show_status()
                
                current_interval = self.interval
                if any(self.consecutive_fails.values()):
                    current_interval = max(self.interval, 3)
                
                if self.is_running:
                    print(f"  等待 {current_interval} 秒...\n")
                    time.sleep(current_interval)
                    
        except Exception as e:
            print(f"\n✗ 运行错误: {e}")
        finally:
            self.stop_all_recordings()
            self.show_status()


def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description='泰山派USB三麦克风录音上传系统',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('-d', '--duration', type=int, default=60,
                       help='每次录音时长（秒），默认60秒')
    parser.add_argument('-i', '--interval', type=int, default=1,
                       help='录音间隔（秒），默认1秒')
    parser.add_argument('-o', '--output', type=str, default='usb_recordings',
                       help='临时输出目录')
    parser.add_argument('-s', '--server', type=str, required=True,
                       help='服务器IP地址')
    parser.add_argument('-p', '--port', type=int, default=8888,
                       help='服务器端口，默认8888')
    
    args = parser.parse_args()
    
    recorder = RecordUploader(
        server_ip=args.server,
        server_port=args.port,
        output_dir=args.output,
        duration=args.duration,
        interval=args.interval
    )
    recorder.run()


if __name__ == "__main__":
    main()