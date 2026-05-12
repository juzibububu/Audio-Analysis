import socket
import os
import sys

def upload_single_file(server_ip, server_port, file_path):
    if not os.path.exists(file_path):
        print(f"文件不存在: {file_path}")
        return False
    
    file_name = os.path.basename(file_path)
    file_size = os.path.getsize(file_path)
    
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.connect((server_ip, server_port))
            
            header = f"{file_name}|{file_size}"
            sock.sendall(header.encode('utf-8'))
            
            response = sock.recv(1024).decode('utf-8')
            if response != 'READY':
                print(f"服务器拒绝: {response}")
                return False
            
            with open(file_path, 'rb') as f:
                bytes_sent = 0
                while bytes_sent < file_size:
                    chunk = f.read(4096)
                    if not chunk:
                        break
                    sock.sendall(chunk)
                    bytes_sent += len(chunk)
                    progress = (bytes_sent / file_size) * 100
                    print(f"\r上传进度: {progress:.1f}%", end='')
            
            print(f"\n文件 {file_name} 上传完成")
            return True
            
    except ConnectionRefusedError:
        print("无法连接到服务器，请确保服务器已启动")
        return False
    except Exception as e:
        print(f"上传失败: {e}")
        return False

def upload_folder(server_ip, server_port, folder_path):
    if not os.path.isdir(folder_path):
        print(f"文件夹不存在: {folder_path}")
        return
    
    print(f"开始上传文件夹: {folder_path}")
    files = [f for f in os.listdir(folder_path) if os.path.isfile(os.path.join(folder_path, f))]
    
    if not files:
        print("文件夹为空")
        return
    
    total_files = len(files)
    success_count = 0
    
    for index, filename in enumerate(files, 1):
        file_path = os.path.join(folder_path, filename)
        print(f"\n[{index}/{total_files}] 正在上传: {filename}")
        
        if upload_single_file(server_ip, server_port, file_path):
            success_count += 1
    
    print(f"\n上传完成！成功: {success_count}/{total_files}")

if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("用法: python uploader.py <服务器IP> <端口> <文件或文件夹路径>")
        print("示例1（上传单个文件）: python uploader.py 192.168.1.100 8888 test.txt")
        print("示例2（上传整个文件夹）: python uploader.py 192.168.1.100 8888 ./my_folder")
        sys.exit(1)
    
    server_ip = sys.argv[1]
    server_port = int(sys.argv[2])
    path = sys.argv[3]
    
    if os.path.isdir(path):
        upload_folder(server_ip, server_port, path)
    elif os.path.isfile(path):
        upload_single_file(server_ip, server_port, path)
    else:
        print(f"路径不存在: {path}")