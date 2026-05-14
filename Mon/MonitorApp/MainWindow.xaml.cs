using System;
using System.Collections.Generic;
using System.Linq;
using System.Net.Sockets;
using System.Text;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using Newtonsoft.Json;
using System.Windows.Media;

namespace MonitorApp
{
    public partial class MainWindow : Window
    {
        private System.Timers.Timer _refreshTimer;

        public MainWindow()
        {
            InitializeComponent();
            _refreshTimer = new System.Timers.Timer(3000);
            _refreshTimer.Elapsed += async (sender, e) => await RefreshStatusAsync();
            _refreshTimer.Start();
            Loaded += async (sender, e) => await RefreshStatusAsync();
        }

        private async void btnRefresh_Click(object sender, RoutedEventArgs e)
        {
            await RefreshStatusAsync();
        }

        private async Task RefreshStatusAsync()
        {
            try
            {
                await Dispatcher.InvokeAsync(() =>
                {
                    lblStatus.Text = "正在连接...";
                });

                var status = await GetServerStatus();
                
                await Dispatcher.InvokeAsync(() =>
                {
                    UpdateUI(status);
                    lblStatus.Text = "连接正常";
                    lblLastUpdate.Text = $"最后更新: {DateTime.Now:HH:mm:ss}";
                });
            }
            catch (Exception ex)
            {
                await Dispatcher.InvokeAsync(() =>
                {
                    lblStatus.Text = $"连接失败: {ex.Message}";
                    pnlDevices.Children.Clear();
                    lstFiles.Items.Clear();
                    lblTotalDevices.Content = "0";
                    lblTotalFiles.Content = "0";
                });
            }
        }

        private async Task<ServerStatus> GetServerStatus()
        {
            string ip = txtServerIp.Text;
            int port = int.Parse(txtPort.Text);

            using (var client = new TcpClient())
            {
                await client.ConnectAsync(ip, port);
                
                using (var stream = client.GetStream())
                {
                    byte[] request = Encoding.UTF8.GetBytes("STATUS");
                    await stream.WriteAsync(request, 0, request.Length);
                    
                    var buffer = new byte[4096];
                    int bytesRead = await stream.ReadAsync(buffer, 0, buffer.Length);
                    string response = Encoding.UTF8.GetString(buffer, 0, bytesRead);
                    
                    return JsonConvert.DeserializeObject<ServerStatus>(response);
                }
            }
        }

        private void UpdateUI(ServerStatus status)
        {
            pnlDevices.Children.Clear();
            var allFiles = new List<FileInfo>();

            foreach (var client in status.Clients)
            {
                var deviceCard = CreateDeviceCard(client.Key, client.Value);
                pnlDevices.Children.Add(deviceCard);

                foreach (var file in client.Value.Files)
                {
                    allFiles.Add(new FileInfo
                    {
                        MicId = file.MicId,
                        Name = file.Name,
                        Timestamp = file.Timestamp
                    });
                }
            }

            allFiles.Sort((a, b) => DateTime.Parse(b.Timestamp).CompareTo(DateTime.Parse(a.Timestamp)));
            lstFiles.ItemsSource = allFiles.Take(30);

            lblTotalDevices.Content = status.Clients.Count;
            lblTotalFiles.Content = allFiles.Count;
        }

        private Border CreateDeviceCard(string ip, ClientInfo client)
        {
            bool isOnline = IsOnline(client.LastSeen);
            
            var card = new Border
            {
                Width = 320,
                Height = 220,
                Background = new SolidColorBrush(isOnline ? Color.FromRgb(22, 33, 62) : Color.FromRgb(30, 30, 40)),
                CornerRadius = new CornerRadius(12),
                Margin = new Thickness(15),
                BorderBrush = isOnline ? new SolidColorBrush(Color.FromRgb(0, 212, 255)) : new SolidColorBrush(Color.FromRgb(80, 80, 80)),
                BorderThickness = new Thickness(2)
            };

            var panel = new StackPanel { Margin = new Thickness(20) };

            var header = new StackPanel { Orientation = Orientation.Horizontal, VerticalAlignment = VerticalAlignment.Center };
            var statusDot = new Border
            {
                Style = isOnline ? (Style)Resources["StatusOnline"] : (Style)Resources["StatusOffline"],
                Margin = new Thickness(0, 0, 10, 0)
            };
            var ipText = new TextBlock
            {
                Text = ip,
                FontSize = 16,
                FontWeight = FontWeights.Bold,
                Foreground = new SolidColorBrush(Colors.White)
            };
            header.Children.Add(statusDot);
            header.Children.Add(ipText);
            panel.Children.Add(header);

            panel.Children.Add(new TextBlock { Text = "", Height = 15 });

            var statsPanel = new StackPanel();
            var mics = new[] { "M1", "M2", "M3" };
            
            foreach (var mic in mics)
            {
                var micRow = new StackPanel { Orientation = Orientation.Horizontal };
                
                var micLabel = new TextBlock
                {
                    Text = mic,
                    Width = 40,
                    Foreground = new SolidColorBrush(Color.FromRgb(0, 212, 255)),
                    FontWeight = FontWeights.Bold
                };
                
                var uploads = client.Uploads.ContainsKey(mic) ? client.Uploads[mic] : 0;
                var uploadText = new TextBlock
                {
                    Text = $"上传: {uploads}",
                    Width = 100,
                    Foreground = new SolidColorBrush(Color.FromRgb(0, 200, 81))
                };
                
                micRow.Children.Add(micLabel);
                micRow.Children.Add(uploadText);
                statsPanel.Children.Add(micRow);
            }
            
            panel.Children.Add(statsPanel);

            panel.Children.Add(new TextBlock { Text = "", Height = 10 });

            var lastSeen = new TextBlock
            {
                Text = $"最后活跃: {FormatTime(client.LastSeen)}",
                FontSize = 12,
                Foreground = new SolidColorBrush(Color.FromRgb(160, 160, 160))
            };
            panel.Children.Add(lastSeen);

            card.Child = panel;
            return card;
        }

        private bool IsOnline(string lastSeenStr)
        {
            if (!DateTime.TryParse(lastSeenStr, out DateTime lastSeen))
                return false;
            
            return (DateTime.Now - lastSeen).TotalMinutes < 2;
        }

        private string FormatTime(string timeStr)
        {
            if (!DateTime.TryParse(timeStr, out DateTime time))
                return "未知";
            
            var now = DateTime.Now;
            var diff = now - time;
            
            if (diff.TotalSeconds < 60)
                return $"刚刚";
            if (diff.TotalMinutes < 60)
                return $"{(int)diff.TotalMinutes}分钟前";
            if (diff.TotalHours < 24)
                return $"{(int)diff.TotalHours}小时前";
            
            return time.ToString("MM-dd HH:mm");
        }
    }

    public class ServerStatus
    {
        public string ServerTime { get; set; }
        public Dictionary<string, ClientInfo> Clients { get; set; } = new Dictionary<string, ClientInfo>();
    }

    public class ClientInfo
    {
        public string LastSeen { get; set; }
        public Dictionary<string, int> Recordings { get; set; } = new Dictionary<string, int>();
        public Dictionary<string, int> Uploads { get; set; } = new Dictionary<string, int>();
        public List<FileData> Files { get; set; } = new List<FileData>();
    }

    public class FileData
    {
        public string Name { get; set; }
        public string MicId { get; set; }
        public string Timestamp { get; set; }
    }

    public class FileInfo
    {
        public string MicId { get; set; }
        public string Name { get; set; }
        public string Timestamp { get; set; }
    }
}