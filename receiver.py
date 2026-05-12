import socket
import os

def start_server(port):
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind(('0.0.0.0', port))
    server_socket.listen(5)
    
    print(f"服务端已启动，监听端口 {port}")
    print("等待客户端连接...")
    
    while True:
        client_socket, client_addr = server_socket.accept()
        client_ip = client_addr[0]
        print(f"\n新连接: {client_ip}:{client_addr[1]}")
        
        try:
            header = client_socket.recv(1024).decode('utf-8')
            if not header:
                print("未收到文件信息")
                client_socket.close()
                continue
            
            parts = header.split('|')
            if len(parts) != 3:
                print("无效的文件信息格式")
                client_socket.close()
                continue
            
            mic_id = parts[0]
            file_name = parts[1]
            file_size = int(parts[2])
            
            save_dir = os.path.join('uploads', client_ip, mic_id)
            os.makedirs(save_dir, exist_ok=True)
            
            save_path = os.path.join(save_dir, file_name)
            
            client_socket.sendall('READY'.encode('utf-8'))
            
            with open(save_path, 'wb') as f:
                bytes_received = 0
                while bytes_received < file_size:
                    chunk = client_socket.recv(4096)
                    if not chunk:
                        break
                    f.write(chunk)
                    bytes_received += len(chunk)
                    progress = (bytes_received / file_size) * 100
                    print(f"\r接收进度: {progress:.1f}%", end='')
            
            print(f"\n文件 {file_name} 已保存到 {save_path}")
            
            client_socket.sendall('OK'.encode('utf-8'))
            
        except Exception as e:
            print(f"接收失败: {e}")
        finally:
            client_socket.close()

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) != 2:
        print("用法: python receiver.py <端口>")
        print("示例: python receiver.py 8888")
        sys.exit(1)
    
    port = int(sys.argv[1])
    start_server(port)