#!/usr/bin/env python3
"""
嘉立创泰山派 RTK3566 - 纯USB三麦克风录音脚本（增强稳定版）
解决M1/M2偶尔录制失败的问题
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

class StableUSBMicRecorder:
    def __init__(self, output_dir="usb_recordings", duration=60, interval=1):
        """
        初始化稳定版USB麦克风录音器
        
        Args:
            output_dir: 录音文件保存目录
            duration: 每次录音时长（秒）
            interval: 录音间隔时间（秒）
        """
        self.output_dir = output_dir
        self.duration = duration
        self.interval = interval
        self.is_running = True
        self.recording_processes = {}
        self.mic_devices = {}  # {M1: device_string, M2: device_string, M3: device_string}
        self.record_count = {"M1": 0, "M2": 0, "M3": 0}
        self.fail_count = {"M1": 0, "M2": 0, "M3": 0}
        self.consecutive_fails = {"M1": 0, "M2": 0, "M3": 0}
        self.lock = threading.Lock()
        
        # USB设备持久化配置
        self.usb_config_file = os.path.join(output_dir, "usb_mic_config.json")
        self.device_retry_count = 3  # 失败后重试次数
        self.device_init_delay = 0.5  # 设备初始化延迟（秒）
        
        # 创建输出目录结构
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
            print(f"✓ 创建主录音目录: {output_dir}\n")
        
        # 为每个USB麦克风创建子目录
        for i in range(1, 4):
            mic_dir = os.path.join(output_dir, f"M{i}")
            if not os.path.exists(mic_dir):
                os.makedirs(mic_dir)
                print(f"✓ 创建M{i}录音目录: {mic_dir}")
        
        # 设置信号处理
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
    
    def signal_handler(self, signum, frame):
        """处理中断信号"""
        print("\n\n收到停止信号，正在安全退出...")
        self.is_running = False
        self.stop_all_recordings()
        self.save_device_config()
        sys.exit(0)
    
    def save_device_config(self):
        """保存USB设备配置，用于下次快速恢复"""
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
            print(f"✓ 设备配置已保存: {self.usb_config_file}")
        except Exception as e:
            print(f"⚠ 配置保存失败: {e}")
    
    def load_device_config(self):
        """加载之前保存的设备配置"""
        try:
            if os.path.exists(self.usb_config_file):
                with open(self.usb_config_file, 'r') as f:
                    config = json.load(f)
                print(f"✓ 加载设备配置: {self.usb_config_file}")
                return config
        except Exception as e:
            print(f"⚠ 配置加载失败: {e}")
        return None
    
    def get_usb_device_details(self):
        """获取USB设备的详细信息（包括物理路径）"""
        usb_info = {}
        try:
            # 获取USB设备树信息
            result = subprocess.run(
                ['lsusb', '-t'], 
                capture_output=True, 
                text=True
            )
            
            # 获取详细的USB设备信息
            result_detail = subprocess.run(
                ['lsusb', '-v'], 
                capture_output=True, 
                text=True
            )
            
            # 解析USB音频设备
            current_device = None
            for line in result_detail.stdout.split('\n'):
                if 'Bus' in line and 'Device' in line:
                    current_device = line.strip()
                elif current_device and 'iSerial' in line:
                    serial = line.split()[-1].strip()
                    if serial and serial != '0' and serial != '3':
                        usb_info[current_device] = serial
            
            return usb_info
        except:
            return {}
    
    def detect_usb_mics_stable(self):
        """
        稳定检测USB麦克风设备
        使用多种方法确保检测到所有USB设备
        """
        print("=" * 60)
        print("稳定检测USB麦克风设备...")
        print("=" * 60)
        
        # 初始化设备列表
        usb_devices_found = []
        
        # 方法1: 通过 arecord -l 检测（最可靠）
        print("\n[1] ALSA设备检测:")
        try:
            result = subprocess.run(
                ["arecord", "-l"], 
                capture_output=True, 
                text=True
            )
            
            # 解析设备，跳过板载声卡
            for line in result.stdout.split('\n'):
                # 跳过板载声卡
                if any(keyword in line.lower() for keyword in ['rockchip', 'rk809', 'rk817', 'hdmi']):
                    continue
                
                if 'card' in line and ':' in line and 'device' in line:
                    print(f"  [检测] {line.strip()}")
                    
                    # 提取声卡和设备编号
                    match = re.search(r'card (\d+):.*device (\d+):', line)
                    if match:
                        card_num = match.group(1)
                        device_num = match.group(2)
                        device_id = f"hw:{card_num},{device_num}"
                        plughw_id = f"plughw:{card_num},{device_num}"
                        
                        # 提取设备名称
                        name_match = re.search(r'\[(.+?)\]', line)
                        device_name = name_match.group(1) if name_match else f"设备{card_num}:{device_num}"
                        
                        # 测试设备是否真的可用
                        if self.test_device_quick(plughw_id):
                            usb_devices_found.append({
                                'card': card_num,
                                'device': device_num,
                                'hw_id': device_id,
                                'plughw_id': plughw_id,
                                'name': device_name,
                                'type': 'USB'
                            })
                            print(f"  ✓ 确认可用: {plughw_id} ({device_name})")
                        else:
                            print(f"  ✗ 设备不可用: {plughw_id}")
        except Exception as e:
            print(f"  ✗ ALSA检测失败: {e}")
        
        # 方法2: 通过是arecord -L 获取更多设备
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
                
                # 跳过板载声卡
                if any(keyword in line.lower() for keyword in ['rockchip', 'rk809', 'rk817']):
                    continue
                
                # 检查是否已存在
                if line not in existing_devices and 'plughw:' in line:
                    if self.test_device_quick(line):
                        # 提取卡号
                        match = re.search(r'plughw:(\d+),(\d+)', line)
                        if match:
                            card_num = match.group(1)
                            device_num = match.group(2)
                            usb_devices_found.append({
                                'card': card_num,
                                'device': device_num,
                                'hw_id': f"hw:{card_num},{device_num}",
                                'plughw_id': line,
                                'name': line,
                                'type': 'USB'
                            })
                            print(f"  ✓ 补充发现: {line}")
                            existing_devices.append(line)
        except Exception as e:
            print(f"  ✗ PCM枚举失败: {e}")
        
        # 去重
        seen = set()
        unique_devices = []
        for dev in usb_devices_found:
            key = dev['plughw_id']
            if key not in seen:
                seen.add(key)
                unique_devices.append(dev)
        
        print(f"\n  总计找到 {len(unique_devices)} 个可用USB音频设备")
        
        return unique_devices
    
    def test_device_quick(self, device, duration=1):
        """快速测试设备是否可用"""
        try:
            test_file = f"/tmp/usb_mic_test_{os.getpid()}.wav"
            
            # 使用timeout防止卡死
            result = subprocess.run([
                "arecord", "-D", device,
                "-f", "S16_LE", "-r", "16000", "-c", "1",
                "-d", str(duration), "-q", test_file
            ], capture_output=True, timeout=duration + 3)
            
            success = False
            if os.path.exists(test_file):
                if os.path.getsize(test_file) > 500:  # 至少500字节
                    success = True
                os.remove(test_file)
            
            return success
            
        except subprocess.TimeoutExpired:
            print(f"    ⚠ 设备 {device} 测试超时")
            return False
        except Exception as e:
            print(f"    ⚠ 设备 {device} 测试异常: {e}")
            return False
    
    def assign_mic_devices_stable(self, usb_devices):
        """
        稳定分配USB麦克风给M1/M2/M3
        支持设备持久化和故障恢复
        """
        print("\n" + "=" * 60)
        print("稳定分配USB麦克风设备...")
        print("=" * 60)
        
        # 尝试加载之前成功的配置
        previous_config = self.load_device_config()
        
        self.mic_devices = {}
        mic_labels = ['M1', 'M2', 'M3']
        
        if len(usb_devices) >= 3:
            print("✓ 检测到3个或更多USB设备，优先分配")
            
            # 如果有之前的配置，优先使用之前成功的设备
            if previous_config:
                print("  使用历史成功配置...")
                for i, label in enumerate(mic_labels):
                    if label in previous_config and i < len(usb_devices):
                        # 验证之前的设备是否仍然可用
                        prev_device = previous_config[label]['device']
                        if self.test_device_quick(prev_device):
                            self.mic_devices[label] = {
                                'device': prev_device,
                                'type': 'USB',
                                'description': f'USB麦克风{i+1}(历史)',
                                'last_success': time.time()
                            }
                            print(f"  [{label}] 恢复: {prev_device}")
                            continue
                        else:
                            print(f"  [{label}] 历史设备 {prev_device} 不可用，重新分配")
                
                # 为没有分配的位置分配新设备
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
                        print(f"  [{label}] 新分配: {dev['plughw_id']}")
            else:
                # 没有历史配置，直接分配前3个设备
                for i, label in enumerate(mic_labels):
                    dev = usb_devices[i]
                    self.mic_devices[label] = {
                        'device': dev['plughw_id'],
                        'type': 'USB',
                        'description': f'USB麦克风{i+1}',
                        'last_success': time.time()
                    }
                    print(f"  [{label}] {dev['plughw_id']} ({dev['name']})")
        
        elif len(usb_devices) == 2:
            print("⚠ 检测到2个USB设备")
            
            # 分配M1和M2
            for i in range(2):
                dev = usb_devices[i]
                self.mic_devices[mic_labels[i]] = {
                    'device': dev['plughw_id'],
                    'type': 'USB',
                    'description': f'USB麦克风{i+1}',
                    'last_success': time.time()
                }
                print(f"  [{mic_labels[i]}] {dev['plughw_id']}")
            
            # M3使用第一个设备的子设备（如果有）
            print("  尝试为M3分配设备...")
            for dev in usb_devices:
                # 尝试使用不同的设备参数
                alt_device = f"plughw:{dev['card']},{int(dev['device'])+1}"
                if self.test_device_quick(alt_device):
                    self.mic_devices[mic_labels[2]] = {
                        'device': alt_device,
                        'type': 'USB_ALT',
                        'description': 'USB备用设备',
                        'last_success': time.time()
                    }
                    print(f"  [{mic_labels[2]}] {alt_device} (备用)")
                    break
            
            if mic_labels[2] not in self.mic_devices:
                print(f"  ⚠ M3设备不可用，将重试")
                
        elif len(usb_devices) == 1:
            print("⚠ 仅检测到1个USB设备")
            dev = usb_devices[0]
            self.mic_devices[mic_labels[0]] = {
                'device': dev['plughw_id'],
                'type': 'USB',
                'description': 'USB麦克风1',
                'last_success': time.time()
            }
            print(f"  [{mic_labels[0]}] {dev['plughw_id']}")
            print(f"  ⚠ M2/M3设备不足，请连接更多USB麦克风")
        else:
            print("✗ 未检测到USB麦克风设备")
            return False
        
        # 保存配置
        self.save_device_config()
        
        # 显示最终分配结果
        print("\n最终设备分配:")
        for mic_id in sorted(self.mic_devices.keys()):
            info = self.mic_devices[mic_id]
            print(f"  [{mic_id}] {info['description']}: {info['device']}")
        
        print(f"\n  可用设备数: {len(self.mic_devices)}/3")
        return len(self.mic_devices) > 0
    
    def record_mic_with_retry(self, mic_id, device, timestamp, progress_queue):
        """
        带重试机制的麦克风录音函数
        
        Args:
            mic_id: 麦克风编号
            device: 音频设备名
            timestamp: 时间戳
            progress_queue: 进度队列
        """
        filename = os.path.join(
            self.output_dir, 
            mic_id, 
            f"{timestamp}_{mic_id}.wav"
        )
        
        # 重试循环
        for attempt in range(1, self.device_retry_count + 1):
            try:
                # 设备初始化延迟（第一次尝试跳过，重试时等待）
                if attempt > 1:
                    time.sleep(self.device_init_delay * attempt)
                    print(f"  [{mic_id}] 重试第{attempt}次...")
                
                # 构建录音命令，添加更多稳定性参数
                cmd = [
                    "arecord",
                    "-D", device,
                    "-f", "S16_LE",
                    "-r", "16000",
                    "-c", "1",
                    "-d", str(self.duration),
                    "--buffer-size=4096",      # 增大缓冲区
                    "--period-size=1024",       # 设置周期大小
                    "--nonblock",               # 非阻塞模式
                    filename
                ]
                
                start_time = time.time()
                
                # 启动录音进程
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    bufsize=4096  # 设置缓冲区大小
                )
                
                with self.lock:
                    self.recording_processes[mic_id] = process
                
                # 等待录音完成，添加超时保护
                try:
                    process.wait(timeout=self.duration + 5)
                except subprocess.TimeoutExpired:
                    print(f"  [{mic_id}] 录音超时，强制终止")
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except:
                        process.kill()
                    raise
                
                elapsed = time.time() - start_time
                
                with self.lock:
                    self.recording_processes.pop(mic_id, None)
                
                # 验证录音文件
                if os.path.exists(filename):
                    file_size = os.path.getsize(filename) / 1024  # KB
                    
                    # 检查文件大小是否合理
                    expected_size_min = (self.duration * 16000 * 2) / 1024 * 0.5  # 至少50%的期望大小
                    expected_size_max = (self.duration * 16000 * 2) / 1024 * 1.2  # 最多120%
                    
                    if file_size >= expected_size_min and file_size <= expected_size_max:
                        # 录音成功
                        with self.lock:
                            self.record_count[mic_id] += 1
                            self.consecutive_fails[mic_id] = 0  # 重置连续失败计数
                            self.mic_devices[mic_id]['last_success'] = time.time()
                        
                        progress_queue.put({
                            'mic_id': mic_id,
                            'status': 'success',
                            'filename': os.path.basename(filename),
                            'size': file_size,
                            'elapsed': elapsed,
                            'attempts': attempt
                        })
                        
                        # 如果重试后成功，记录日志
                        if attempt > 1:
                            print(f"  [{mic_id}] 重试成功! (第{attempt}次)")
                        
                        return True
                    else:
                        # 文件大小异常
                        error_msg = f"文件大小异常: {file_size:.1f}KB (期望{expected_size_min:.1f}-{expected_size_max:.1f}KB)"
                        if attempt == self.device_retry_count:
                            progress_queue.put({
                                'mic_id': mic_id,
                                'status': 'failed',
                                'error': error_msg
                            })
                        else:
                            print(f"  [{mic_id}] {error_msg}，准备重试...")
                        
                        # 删除异常文件
                        try:
                            os.remove(filename)
                        except:
                            pass
                else:
                    # 文件未创建
                    if attempt == self.device_retry_count:
                        progress_queue.put({
                            'mic_id': mic_id,
                            'status': 'failed',
                            'error': '录音文件未创建'
                        })
                
            except Exception as e:
                error_msg = str(e)
                if attempt == self.device_retry_count:
                    progress_queue.put({
                        'mic_id': mic_id,
                        'status': 'error',
                        'error': error_msg
                    })
                else:
                    print(f"  [{mic_id}] 异常: {error_msg}，准备重试...")
        
        # 所有重试都失败
        with self.lock:
            self.fail_count[mic_id] += 1
            self.consecutive_fails[mic_id] += 1
            
            # 如果连续失败超过5次，尝试切换设备
            if self.consecutive_fails[mic_id] >= 5:
                print(f"  ⚠ [{mic_id}] 连续失败{self.consecutive_fails[mic_id]}次，尝试切换设备...")
                self.try_alternative_device(mic_id)
        
        return False
    
    def try_alternative_device(self, mic_id):
        """尝试为失败的麦克风切换到备用设备"""
        print(f"  [{mic_id}] 寻找备用设备...")
        
        # 获取所有可用设备
        try:
            result = subprocess.run(
                ["arecord", "-l"], 
                capture_output=True, 
                text=True
            )
            
            # 查找其他可用的USB设备
            current_device = self.mic_devices.get(mic_id, {}).get('device', '')
            
            for line in result.stdout.split('\n'):
                if 'card' in line and 'usb' in line.lower() and 'device' in line:
                    match = re.search(r'card (\d+):.*device (\d+):', line)
                    if match:
                        alt_device = f"plughw:{match.group(1)},{match.group(2)}"
                        if alt_device != current_device and self.test_device_quick(alt_device):
                            print(f"  [{mic_id}] 切换到备用设备: {alt_device}")
                            self.mic_devices[mic_id]['device'] = alt_device
                            self.mic_devices[mic_id]['last_success'] = time.time()
                            self.consecutive_fails[mic_id] = 0
                            self.save_device_config()
                            return True
        except:
            pass
        
        # 如果没有备用设备，尝试重置当前设备
        print(f"  [{mic_id}] 尝试重置设备...")
        time.sleep(1)  # 等待设备恢复
        self.consecutive_fails[mic_id] = 0  # 重置计数器
        return False
    
    def stop_all_recordings(self):
        """停止所有录音进程"""
        with self.lock:
            for mic_id, process in list(self.recording_processes.items()):
                try:
                    if process.poll() is None:  # 进程仍在运行
                        process.terminate()
                        process.wait(timeout=3)
                except:
                    try:
                        process.kill()
                    except:
                        pass
        
        # 清理临时文件
        try:
            for f in os.listdir('/tmp/'):
                if f.startswith('usb_mic_test_') and f.endswith('.wav'):
                    os.remove(os.path.join('/tmp/', f))
        except:
            pass
    
    def show_detailed_status(self):
        """显示详细状态"""
        with self.lock:
            total_success = sum(self.record_count.values())
            total_fail = sum(self.fail_count.values())
            
            print(f"\n{'='*60}")
            print(f"USB麦克风录音详细状态")
            print(f"{'='*60}")
            
            for mic_id in ['M1', 'M2', 'M3']:
                success = self.record_count[mic_id]
                fails = self.fail_count[mic_id]
                consecutive = self.consecutive_fails[mic_id]
                total = success + fails
                
                if total > 0:
                    success_rate = (success / total) * 100
                    status = "✓" if consecutive == 0 else "⚠" if consecutive < 3 else "✗"
                    print(f"  {status} {mic_id}: {success}/{total} ({success_rate:.1f}%) | 连续失败:{consecutive}")
                else:
                    print(f"  - {mic_id}: 未录音")
            
            print(f"  总成功: {total_success} | 总失败: {total_fail}")
            
            if total_success + total_fail > 0:
                overall_rate = (total_success / (total_success + total_fail)) * 100
                print(f"  整体成功率: {overall_rate:.1f}%")
            
            # 磁盘空间
            total_size = 0
            for mic_id in ['M1', 'M2', 'M3']:
                mic_dir = os.path.join(self.output_dir, mic_id)
                if os.path.exists(mic_dir):
                    for file in os.listdir(mic_dir):
                        if file.endswith('.wav'):
                            total_size += os.path.getsize(os.path.join(mic_dir, file))
            
            print(f"  总大小: {total_size/(1024**2):.2f} MB")
            
            try:
                stat = os.statvfs(self.output_dir)
                free_gb = (stat.f_frsize * stat.f_bavail) / (1024**3)
                print(f"  剩余空间: {free_gb:.2f} GB")
            except:
                pass
            print(f"{'='*60}\n")
    
    def run(self):
        """主运行循环"""
        print("\n" + "=" * 60)
        print("  泰山派稳定版USB三麦克风录音系统")
        print("=" * 60)
        print(f"  录音时长: {self.duration}秒")
        print(f"  录音间隔: {self.interval}秒")
        print(f"  音频格式: 16kHz, 16bit, Mono, WAV")
        print(f"  重试次数: {self.device_retry_count}")
        print(f"  保存目录: {os.path.abspath(self.output_dir)}")
        print(f"  设备要求: 仅使用USB麦克风")
        print("  按 Ctrl+C 停止程序")
        print("=" * 60 + "\n")
        
        # 检测USB麦克风
        usb_devices = self.detect_usb_mics_stable()
        
        if not usb_devices:
            print("\n" + "=" * 60)
            print("✗✗✗ 致命错误: 未检测到任何USB麦克风设备 ✗✗✗")
            print("=" * 60)
            print("\n请检查:")
            print("  1. USB麦克风是否正确插入")
            print("  2. USB口供电是否充足（考虑使用带电源的USB集线器）")
            print("  3. 运行 'lsusb' 查看USB设备")
            print("  4. 运行 'dmesg | grep -i usb' 查看USB日志")
            print("\n程序退出。")
            return
        
        # 分配设备
        if not self.assign_mic_devices_stable(usb_devices):
            print("\n✗ 无法分配足够的USB麦克风设备")
            return
        
        # 详细测试所有设备
        print("\n详细测试USB麦克风设备:")
        all_ok = True
        for mic_id, info in self.mic_devices.items():
            print(f"  测试 [{mic_id}] {info['device']}...", end=' ')
            if self.test_device_quick(info['device'], duration=2):
                print("✓ 正常")
            else:
                print("✗ 失败")
                all_ok = False
        
        if not all_ok:
            print("\n⚠ 部分设备测试失败，将启用自动恢复机制")
        
        print("\n开始录音...\n")
        
        session_start = time.time()
        last_status_time = time.time()
        
        try:
            while self.is_running:
                # 生成统一时间戳
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 开始新一组录音 ({timestamp})")
                
                progress_queue = Queue()
                threads = []
                
                # 为每个USB麦克风创建录音线程
                for mic_id, info in self.mic_devices.items():
                    thread = threading.Thread(
                        target=self.record_mic_with_retry,
                        args=(mic_id, info['device'], timestamp, progress_queue),
                        daemon=True
                    )
                    thread.start()
                    threads.append((mic_id, thread))
                    time.sleep(self.device_init_delay)  # 错开启动时间
                
                # 等待所有线程完成
                for mic_id, thread in threads:
                    thread.join(timeout=self.duration + 15)
                
                # 收集结果
                results = []
                while not progress_queue.empty():
                    results.append(progress_queue.get())
                
                results.sort(key=lambda x: x.get('mic_id', ''))
                
                # 显示结果
                success_count = 0
                for result in results:
                    mic_id = result.get('mic_id', 'Unknown')
                    status = result.get('status', 'unknown')
                    
                    if status == 'success':
                        success_count += 1
                        attempts = result.get('attempts', 1)
                        retry_info = f" (重试{attempts}次)" if attempts > 1 else ""
                        print(f"  ✓ {mic_id}: {result['filename']} "
                              f"({result['size']:.1f} KB){retry_info}")
                    else:
                        print(f"  ✗ {mic_id}: {'失败' if status == 'failed' else '错误'} "
                              f"- {result.get('error', '未知错误')}")
                
                print(f"  录音完成: {success_count}/{len(self.mic_devices)} 成功")
                
                # 定期显示详细状态（每5次或每5分钟）
                if time.time() - last_status_time > 300 or sum(self.record_count.values()) % 5 == 0:
                    self.show_detailed_status()
                    last_status_time = time.time()
                
                # 智能间隔：如果有失败，增加间隔时间让设备恢复
                current_interval = self.interval
                if any(self.consecutive_fails.values()):
                    current_interval = max(self.interval, 3)  # 至少3秒
                    print(f"  检测到失败，延长间隔至 {current_interval} 秒")
                
                if self.is_running:
                    print(f"  等待 {current_interval} 秒...\n")
                    time.sleep(current_interval)
                    
        except KeyboardInterrupt:
            pass
        except Exception as e:
            print(f"\n✗ 运行错误: {e}")
        finally:
            self.stop_all_recordings()
            
            elapsed = time.time() - session_start
            print("\n" + "=" * 60)
            print("  USB麦克风录音结束")
            print("=" * 60)
            print(f"  运行时长: {elapsed/60:.1f} 分钟")
            self.show_detailed_status()
            print(f"  文件保存位置:")
            for mic_id in sorted(self.mic_devices.keys()):
                mic_dir = os.path.join(os.path.abspath(self.output_dir), mic_id)
                if os.path.exists(mic_dir):
                    file_count = len([f for f in os.listdir(mic_dir) if f.endswith('.wav')])
                    print(f"    {mic_id}: {mic_dir} ({file_count} 个文件)")
            print("=" * 60)


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='泰山派稳定版USB三麦克风录音系统',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 默认设置
  python3 stable_usb_recorder.py
  
  # 自定义参数
  python3 stable_usb_recorder.py -d 30 -i 5
  
  # 增加重试次数
  python3 stable_usb_recorder.py --retry 5
        """
    )
    parser.add_argument('-d', '--duration', type=int, default=60,
                       help='每次录音时长（秒），默认60秒')
    parser.add_argument('-i', '--interval', type=int, default=1,
                       help='录音间隔（秒），默认1秒')
    parser.add_argument('-o', '--output', type=str, default='usb_recordings',
                       help='输出根目录，默认usb_recordings')
    parser.add_argument('--retry', type=int, default=3,
                       help='失败重试次数，默认3次')
    
    args = parser.parse_args()
    
    recorder = StableUSBMicRecorder(
        output_dir=args.output,
        duration=args.duration,
        interval=args.interval
    )
    recorder.device_retry_count = args.retry
    recorder.run()


if __name__ == "__main__":
    main()