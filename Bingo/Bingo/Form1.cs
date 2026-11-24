using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Data;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Media;
using System.Threading.Tasks;
using System.Windows.Forms;
using System.Windows.Media;
using static System.Windows.Forms.VisualStyles.VisualStyleElement.TrayNotify;

namespace Bingo
{
    public partial class Form1 : Form
    {
        private SoundPlayer player;
        private SoundPlayer final;
        private Random rand = new Random();
        private const int TotalNumbers = 36;

        private MediaPlayer backgroundPlayer;

        private readonly string imagesongPath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "..", "..", "imagesong");

        private string GetFullPath(string fileName) => Path.Combine(imagesongPath, fileName);
        private string GetRoomFilePath(string roomNumber) => Path.Combine(imagesongPath, $"room_{roomNumber}.txt");

        public Form1()
        {
            InitializeComponent();
            InitializeAudio();

            // 💡 แก้ไขปัญหาโปร่งแสง (Transparent Bug Fix)
            if (pictureBox39 != null)
            {
                // 1. ตั้งค่า BackColor เป็น Transparent
                pictureBox39.BackColor = System.Drawing.Color.Transparent;

                // 2. บังคับเปลี่ยน Parent ให้เป็น Form โดยตรง (this) 
                //    เพื่อให้มองทะลุ Panel/GroupBox ไปเห็นพื้นหลัง Form
                pictureBox39.Parent = this;

                // 3. นำมาอยู่ด้านหน้าสุด
                pictureBox39.BringToFront();
            }
        }

        #region Initialization
        // ... (โค้ดส่วน LoadImageSafe และ InitializeAudio เหมือนเดิม)
        private Image LoadImageSafe(string path)
        {
            if (File.Exists(path))
            {
                try
                {
                    using (var fs = new FileStream(path, FileMode.Open, FileAccess.Read))
                    using (var ms = new MemoryStream())
                    {
                        fs.CopyTo(ms);
                        ms.Position = 0;
                        return Image.FromStream(ms);
                    }
                }
                catch (Exception ex)
                {
                    MessageBox.Show($"ไม่สามารถโหลดรูปภาพ: {path}\nข้อผิดพลาด: {ex.Message}", "ข้อผิดพลาดรูปภาพ", MessageBoxButtons.OK, MessageBoxIcon.Error);
                    return null;
                }
            }
            return null;
        }

        private void InitializeAudio()
        {
            string beep1Path = GetFullPath("beep1.wav");
            string beep2Path = GetFullPath("beep2.wav");
            string fullPath = GetFullPath("dsbingo.mp3");

            if (!Directory.Exists(imagesongPath))
            {
                MessageBox.Show(
                    $"ไม่พบโฟลเดอร์ 'imagesong'!\n" +
                    $"โปรแกรมกำลังค้นหาโฟลเดอร์ที่ตำแหน่งนี้:\n{imagesongPath}\n\n" +
                    $"โปรดนำโฟลเดอร์ imagesong ไปวางใน Project Root (ระดับเดียวกับ bin)",
                    "🛑 ข้อผิดพลาด Path", MessageBoxButtons.OK, MessageBoxIcon.Stop);
                return;
            }

            if (File.Exists(beep1Path)) player = new SoundPlayer(beep1Path);
            if (File.Exists(beep2Path)) final = new SoundPlayer(beep2Path);

            if (File.Exists(fullPath))
            {
                try
                {
                    backgroundPlayer = new MediaPlayer();
                    backgroundPlayer.Open(new Uri(fullPath));

                    if (volumeTrackBar != null)
                    {
                        backgroundPlayer.Volume = volumeTrackBar.Value / 100.0;
                    }
                    else
                    {
                        backgroundPlayer.Volume = 0.5;
                    }

                    backgroundPlayer.MediaEnded += (sender, e) =>
                    {
                        backgroundPlayer.Position = TimeSpan.Zero;
                        backgroundPlayer.Play();
                    };

                    backgroundPlayer.Play();
                }
                catch (Exception ex)
                {
                    MessageBox.Show(
                        $"ไม่สามารถเล่นไฟล์ dsbingo.mp3 ได้\n" +
                        $"ข้อผิดพลาด: {ex.Message}\n" +
                        $"โปรดตรวจสอบว่าได้เพิ่ม Reference: PresentationCore และ WindowsBase แล้ว",
                        "ข้อผิดพลาดไฟล์สื่อ", MessageBoxButtons.OK, MessageBoxIcon.Error);
                }
            }
            else
            {
                MessageBox.Show($"ไม่พบไฟล์ dsbingo.mp3\nโปรแกรมกำลังค้นหาที่: {fullPath}", "ข้อผิดพลาดไฟล์เสียง MP3", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }

        /// <summary>
        /// ตั้งค่าภาพเริ่มต้น: Grid (1-36) เป็น d.png, Center (37) เป็น d.png
        /// </summary>
        public void loadimage()
        {
            string gridDefaultPath = GetFullPath("d.png");
            string centerDefaultPath = GetFullPath("c.png");

            for (int i = 1; i <= TotalNumbers; i++)
            {
                string pictureBoxName = "pictureBox" + i.ToString();
                Control[] foundControls = this.Controls.Find(pictureBoxName, true);

                if (foundControls.Length > 0 && foundControls[0] is PictureBox currentPictureBox)
                {
                    currentPictureBox.Image = LoadImageSafe(gridDefaultPath);
                }
            }
            if (pictureBox37 != null)
            {
                pictureBox37.Image = LoadImageSafe(centerDefaultPath);
            }
        }

        public void loadnopicture(string parts, string no)
        {
            string numberImagePath = GetFullPath(no + ".png");
            string pictureBoxName = "pictureBox" + parts;
            Control[] foundControls = this.Controls.Find(pictureBoxName, true);

            if (foundControls.Length > 0 && foundControls[0] is PictureBox currentPictureBox)
            {
                currentPictureBox.Image = LoadImageSafe(numberImagePath);
            }

            if (int.TryParse(parts, out int partNumber) && partNumber >= TotalNumbers)
            {
                if (btnRandom != null) btnRandom.Enabled = false;
                if (btnReset != null) btnReset.Enabled = true;
                if (btnResetall != null) btnResetall.Enabled = true;
            }
            else
            {
                if (btnReset != null) btnReset.Enabled = true;
                if (btnResetall != null) btnResetall.Enabled = true;
                if (btnRandom != null) btnRandom.Enabled = true;
            }
        }

        /// <summary>
        /// ปรับปรุงการสร้าง Bitmap เพื่อคงความโปร่งใสของภาพหมุน
        /// </summary>
        public System.Drawing.Image RotateImage(System.Drawing.Image img, float angle)
        {
            Bitmap rotatedImage = new Bitmap(img.Width, img.Height, System.Drawing.Imaging.PixelFormat.Format32bppArgb);

            using (System.Drawing.Graphics g = System.Drawing.Graphics.FromImage(rotatedImage))
            {
                g.Clear(System.Drawing.Color.Transparent);
                g.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.HighQuality;
                g.InterpolationMode = System.Drawing.Drawing2D.InterpolationMode.HighQualityBicubic;
                g.TranslateTransform(img.Width / 2, img.Height / 2);
                g.RotateTransform(angle % 360);
                g.TranslateTransform(-img.Width / 2, -img.Height / 2);
                g.DrawImage(img, new Point(0, 0));
            }
            return rotatedImage;
        }

        #endregion

        #region Event Handlers

        private void Form1_Load(object sender, EventArgs e)
        {
            // 1. กำหนดให้ pbCharacter มีแม่ (Parent) เป็น pbBackground
            pictureBox39.Parent = pictureBox37;

            // 2. ตั้งค่าพื้นหลังของ pbCharacter ให้เป็น Transparent
            //    (ตอนนี้ "Transparent" จะหมายถึงให้ใช้พื้นหลังของ pbBackground)
            pictureBox39.BackColor = System.Drawing.Color.Transparent;

            // 3. (สำคัญ) ปรับตำแหน่งของ pbCharacter ใหม่
            //    เพราะตอนนี้ตำแหน่ง (0, 0) ของมันจะอิงจากมุมบนซ้ายของ pbBackground
            //    ไม่ใช่จากฟอร์ม
            pictureBox39.Location = new Point(1, 1); // ย้ายไปที่ x=50, y=50 (นับจากขอบของ pbBackground)


            if (btnRandom != null) btnRandom.Enabled = false;
            if (btnReset != null) btnReset.Enabled = false;
            if (btnResetall != null) btnResetall.Enabled = false;

            for (int i = 1; i <= 14; i++)
            {
                if (listBox1 != null) listBox1.Items.Add($"ห้อง {i}");
            }

            loadimage();
        }

        private void volumeTrackBar_Scroll(object sender, EventArgs e)
        {
            if (backgroundPlayer != null && volumeTrackBar != null)
            {
                backgroundPlayer.Volume = volumeTrackBar.Value / 100.0;
            }
        }

        private void listBox1_SelectedIndexChanged(object sender, EventArgs e)
        {
            LoadRoomData();
        }

        private void listBox1_KeyDown(object sender, KeyEventArgs e)
        {
            if (e.KeyCode == Keys.Delete)
            {
                if (listBox1 == null || listBox1.SelectedIndex == -1) return;

                string roomItem = listBox1.SelectedItem.ToString();
                string roomNumber = roomItem.Split(' ')[1];
                string currentFilePath = GetRoomFilePath(roomNumber);

                DialogResult confirmResult = MessageBox.Show(
                    $"คุณต้องการลบข้อมูลทั้งหมดของ {roomItem} ในไฟล์ {Path.GetFileName(currentFilePath)} ใช่หรือไม่?\n" +
                    $"การกระทำนี้ไม่สามารถยกเลิกได้",
                    "ยืนยันการลบข้อมูลห้อง",
                    MessageBoxButtons.YesNo,
                    MessageBoxIcon.Warning
                );

                if (confirmResult == DialogResult.Yes)
                {
                    try
                    {
                        if (File.Exists(currentFilePath))
                        {
                            File.WriteAllText(currentFilePath, string.Empty);
                            MessageBox.Show($"ล้างข้อมูลของ {roomItem} เรียบร้อยแล้ว", "สำเร็จ", MessageBoxButtons.OK, MessageBoxIcon.Information);

                            if (listBox2 != null) listBox2.Items.Clear();

                            LoadRoomData();
                        }
                        else
                        {
                            MessageBox.Show($"ไม่พบไฟล์ข้อมูลสำหรับ {roomItem}: {Path.GetFileName(currentFilePath)}", "ข้อผิดพลาด", MessageBoxButtons.OK, MessageBoxIcon.Error);
                        }
                    }
                    catch (Exception ex)
                    {
                        MessageBox.Show($"เกิดข้อผิดพลาดในการลบ/ล้างไฟล์: {ex.Message}", "ข้อผิดพลาด", MessageBoxButtons.OK, MessageBoxIcon.Error);
                    }
                }
            }
        }

        private async void btnRandom_Click(object sender, EventArgs e)
        {
            if (listBox1 == null || listBox1.SelectedIndex == -1)
            {
                MessageBox.Show("กรุณาเลือกห้องที่ต้องการ");
                return;
            }

            if (btnRandom != null) btnRandom.Enabled = false;
            string room = listBox1.SelectedItem.ToString().Split(' ')[1];
            string currentFilePath = GetRoomFilePath(room);

            var existingNumbers = new bool[TotalNumbers + 1];
            if (File.Exists(currentFilePath))
            {
                string[] lines = File.ReadAllLines(currentFilePath);
                foreach (var line in lines)
                {
                    string[] parts = line.Split(':');
                    if (parts.Length > 2 && int.TryParse(parts[2].Trim(), out int num) && num >= 1 && num <= TotalNumbers)
                    {
                        existingNumbers[num] = true;
                    }
                }
            }

            int newNumber;
            do
            {
                newNumber = rand.Next(1, TotalNumbers + 1);
            } while (existingNumbers[newNumber]);

            string centerSpinPath = GetFullPath("c.png");
            if (pictureBox37 != null)
            {
                pictureBox37.Image = LoadImageSafe(centerSpinPath);
            }
            // ตั้งค่า pictureBox39 เป็น Spinner Default เมื่อเริ่ม Random
            if (pictureBox39 != null)
            {
                pictureBox39.Image = LoadImageSafe(centerSpinPath);
            }

            await SpinWheelAnimation(newNumber);

            int no = listBox2 != null ? listBox2.Items.Count + 1 : 1;
            string newData = $"{no} : {DateTime.Now.ToString("MM-dd")} : {newNumber}";

            File.AppendAllText(currentFilePath, newData + Environment.NewLine);

            if (listBox2 != null)
            {
                listBox2.Items.Insert(0, newData);
                listBox2.SelectedIndex = 0;
            }

            loadnopicture(no.ToString(), newNumber.ToString());
            if (final != null) final.Play();

            if (btnRandom != null) btnRandom.Enabled = true;
            if (btnReset != null) btnReset.Enabled = true;
            if (btnResetall != null) btnResetall.Enabled = true;
        }

        private void btnReset_Click(object sender, EventArgs e)
        {
            if (listBox1 == null || listBox1.SelectedIndex == -1)
            {
                MessageBox.Show("กรุณาเลือกห้องที่ต้องการ");
                return;
            }

            DialogResult result = MessageBox.Show("คุณต้องการลบข้อมูลทั้งหมดในห้องนี้ใช่หรือไม่?", "ยืนยันการลบ", MessageBoxButtons.YesNo, MessageBoxIcon.Warning);
            if (result == DialogResult.Yes)
            {
                const string correctPassword = "12341";
                try
                {
                    using (PasswordForm passwordPrompt = new PasswordForm())
                    {
                        if (passwordPrompt.ShowDialog() == DialogResult.OK)
                        {
                            string enteredPassword = passwordPrompt.Password;
                            if (enteredPassword == correctPassword)
                            {
                                string room = listBox1.SelectedItem.ToString().Split(' ')[1];
                                string currentFilePath = GetRoomFilePath(room);

                                if (File.Exists(currentFilePath))
                                {
                                    File.WriteAllText(currentFilePath, string.Empty);
                                    if (listBox2 != null) listBox2.Items.Clear();

                                    loadimage();

                                    // ล้าง pictureBox39 เมื่อรีเซ็ตห้อง
                                    if (pictureBox39 != null) pictureBox39.Image = null;

                                    MessageBox.Show($"ล้างข้อมูลในไฟล์ room_{room}.txt เรียบร้อยแล้ว", "สำเร็จ", MessageBoxButtons.OK, MessageBoxIcon.Information);
                                }
                                else
                                {
                                    MessageBox.Show("ไม่พบไฟล์ข้อมูลสำหรับห้องนี้", "ข้อผิดพลาด", MessageBoxButtons.OK, MessageBoxIcon.Error);
                                }
                            }
                            else
                            {
                                MessageBox.Show("รหัสผ่านไม่ถูกต้อง การดำเนินการถูกยกเลิก", "ข้อผิดพลาด", MessageBoxButtons.OK, MessageBoxIcon.Error);
                            }
                        }
                    }
                }
                catch (TypeLoadException)
                {
                    MessageBox.Show("ไม่พบคลาส PasswordForm โปรดตรวจสอบไฟล์ PasswordForm.cs", "ข้อผิดพลาดคลาส", MessageBoxButtons.OK, MessageBoxIcon.Error);
                }
            }

            if (btnRandom != null) btnRandom.Enabled = true;
        }

        private void btnResetall_Click(object sender, EventArgs e)
        {
            const string correctPassword = "DS123456XXX";
            DialogResult result = MessageBox.Show("คุณแน่ใจหรือไม่ที่จะลบข้อมูลทั้งหมดในไฟล์", "ยืนยันการลบ", MessageBoxButtons.YesNo, MessageBoxIcon.Warning);

            if (result == DialogResult.Yes)
            {
                try
                {
                    using (var passwordPrompt = new PasswordForm())
                    {
                        if (passwordPrompt.ShowDialog() == DialogResult.OK)
                        {
                            string enteredPassword = passwordPrompt.Password;
                            if (enteredPassword == correctPassword)
                            {
                                for (int i = 1; i <= 14; i++)
                                {
                                    string fileName = GetRoomFilePath(i.ToString());
                                    if (File.Exists(fileName))
                                    {
                                        File.WriteAllText(fileName, string.Empty);
                                    }
                                }

                                loadimage();

                                // ล้าง pictureBox39 เมื่อรีเซ็ตทั้งหมด
                                if (pictureBox39 != null) pictureBox39.Image = null;

                                if (listBox2 != null) listBox2.Items.Clear();
                                if (btnResetall != null) btnResetall.Enabled = false;

                                MessageBox.Show("ลบข้อมูลในไฟล์ทั้งหมดเรียบร้อยแล้ว", "สำเร็จ", MessageBoxButtons.OK, MessageBoxIcon.Information);
                            }
                            else
                            {
                                MessageBox.Show("รหัสผ่านไม่ถูกต้อง การดำเนินการถูกยกเลิก", "ข้อผิดพลาด", MessageBoxButtons.OK, MessageBoxIcon.Error);
                            }
                        }
                    }
                }
                catch (TypeLoadException)
                {
                    MessageBox.Show("ไม่พบคลาส PasswordForm โปรดตรวจสอบไฟล์ PasswordForm.cs", "ข้อผิดพลาดคลาส", MessageBoxButtons.OK, MessageBoxIcon.Error);
                }
            }
        }

        private void button1_Click(object sender, EventArgs e)
        {
            if (textBox1 == null)
            {
                MessageBox.Show("ไม่พบ TextBox1", "ข้อผิดพลาด", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return;
            }
            string newNumberStr = textBox1.Text.Trim();
            if (!int.TryParse(newNumberStr, out int numberValue) || numberValue < 1 || numberValue > TotalNumbers)
            {
                string errorMessage = string.IsNullOrWhiteSpace(newNumberStr) ?
                "กรุณากรอกข้อมูลในช่อง TextBox1 ก่อน" :
                                          (!int.TryParse(newNumberStr, out _) ? "ข้อมูลที่กรอกต้องเป็นตัวเลขเท่านั้น" :
                                          "ตัวเลขที่กรอกต้องอยู่ในช่วง 1 ถึง 36");
                MessageBox.Show(errorMessage, "ข้อผิดพลาด", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }

            if (listBox1 == null || listBox1.SelectedItem == null)
            {
                MessageBox.Show("กรุณาเลือกห้องใน ListBox1 ก่อนทำการบันทึกข้อมูล", "ข้อผิดพลาด", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }

            string roomNumber = listBox1.SelectedItem.ToString().Split(' ')[1];
            string currentFilePath = GetRoomFilePath(roomNumber);

            int no = listBox2 != null ? listBox2.Items.Count + 1 : 1;
            string newData = $"{no} : {DateTime.Now.ToString("MM-dd")} : {newNumberStr}";
            File.AppendAllText(currentFilePath, newData + Environment.NewLine);

            if (listBox2 != null) listBox2.Items.Insert(0, newData);
            textBox1.Text = string.Empty;

            if (final != null) final.Play();
            loadnopicture(no.ToString(), newNumberStr);

            // โหลด aN.png เมื่อมีการใส่เลขมือ (TextBox1)
            string finalImagePath39 = GetFullPath($"{newNumberStr}.png");
            string finalImagePath391 = GetFullPath($"a{newNumberStr}.png");
            System.Drawing.Image finalImage39 = LoadImageSafe(finalImagePath39);
            System.Drawing.Image finalImage391 = LoadImageSafe(finalImagePath391);
            if (pictureBox39 != null && finalImage39 != null)
            {
                pictureBox39.Image = finalImage39;
                pictureBox37.Image = finalImage391;
            }
        }

        #endregion

        #region Logic & Utility

        private void LoadRoomData()
        {
            if (listBox1 == null || listBox1.SelectedItem == null) return;
            string room = listBox1.SelectedItem.ToString().Split(' ')[1];
            string currentFilePath = GetRoomFilePath(room);

            if (listBox2 != null) listBox2.Items.Clear();

            // 1. รีเซ็ตภาพ Grid (1-36) เป็น d.png, Center (37) เป็น d.png
            loadimage();

            // ตั้งค่า pictureBox39 เป็น null (ว่างเปล่า) เสมอเมื่อโหลดห้องใหม่
            if (pictureBox39 != null)
            {
                pictureBox39.Image = null;
            }

            bool hasData = false;
            if (File.Exists(currentFilePath))
            {
                string[] lines = File.ReadAllLines(currentFilePath);

                // 2. วนลูปย้อนหลังเพื่อแสดงผลใน ListBox2 และอัปเดต Grid (pictureBox1-36)
                for (int i = lines.Length - 1; i >= 0; i--)
                {
                    string line = lines[i];
                    string[] parts = line.Split(':');

                    if (parts.Length > 2 && int.TryParse(parts[0].Trim(), out int partNum) && int.TryParse(parts[2].Trim(), out int number))
                    {
                        // loadnopicture จะโหลดภาพเลขที่ออกไปยัง pictureBox1-36
                        loadnopicture(partNum.ToString(), number.ToString());
                        if (listBox2 != null) listBox2.Items.Add(line);
                        hasData = true;
                    }
                }

                // *** ส่วนที่เคยโหลดภาพสุดท้ายเข้า pictureBox37 และ pictureBox39 ถูกนำออกแล้ว ***
            }

            if (!hasData)
            {
                if (btnRandom != null) btnRandom.Enabled = true;
                if (btnReset != null) btnReset.Enabled = false;
                if (btnResetall != null) btnResetall.Enabled = false;
            }
            else
            {
                if (btnRandom != null) btnRandom.Enabled = true;
                if (btnReset != null) btnReset.Enabled = true;
                if (btnResetall != null) btnResetall.Enabled = true;
            }
        }

        private void listBox2_KeyDown(object sender, System.Windows.Forms.KeyEventArgs e)
        {
            if (e.KeyCode != Keys.Delete) return;
            e.Handled = true;
            if (listBox1 == null || listBox1.SelectedItem == null)
            {
                MessageBox.Show("กรุณาเลือกห้องใน ListBox1 ก่อน เพื่อระบุไฟล์ที่ต้องการแก้ไข", "ข้อผิดพลาด", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }

            if (listBox2 == null || listBox2.SelectedIndex == -1) return;
            DialogResult confirmResult = MessageBox.Show("คุณต้องการลบรายการที่เลือกนี้ใช่หรือไม่? ลำดับในรายการจะถูกแก้ไขใหม่ทั้งหมด", "ยืนยันการลบรายการและแก้ไขลำดับ", MessageBoxButtons.YesNo, MessageBoxIcon.Question);
            if (confirmResult == DialogResult.Yes)
            {
                string selectedRoomItem = listBox1.SelectedItem.ToString();
                string[] roomParts = selectedRoomItem.Split(' ');
                string roomNumber = (roomParts.Length >= 2) ? roomParts[1] : null;

                if (string.IsNullOrEmpty(roomNumber)) return;
                string currentFilePath = GetRoomFilePath(roomNumber);

                string itemToDelete = listBox2.SelectedItem.ToString();

                try
                {
                    List<string> lines = new List<string>(File.ReadAllLines(currentFilePath));
                    if (lines.Remove(itemToDelete))
                    {
                        // ทำการรัน no ใหม่ (re-index)
                        for (int i = 0; i < lines.Count; i++)
                        {
                            string oldLine = lines[i];
                            int firstColonIndex = oldLine.IndexOf(':');
                            if (firstColonIndex != -1)
                            {
                                string contentPart = oldLine.Substring(firstColonIndex);
                                lines[i] = $"{i + 1}{contentPart}";
                            }
                        }

                        File.WriteAllLines(currentFilePath, lines);
                        if (listBox2 != null) listBox2.Items.Clear();

                        // 1. รีเซ็ตภาพ Grid (1-36) เป็น d.png, Center (37) เป็น d.png
                        loadimage();

                        // ตั้งค่า pictureBox39 เป็น null (ว่างเปล่า)
                        if (pictureBox39 != null)
                        {
                            pictureBox39.Image = null;
                        }

                        bool hasData = false;
                        for (int i = lines.Count - 1; i >= 0; i--)
                        {
                            string updatedLine = lines[i];
                            if (listBox2 != null) listBox2.Items.Add(updatedLine);

                            string[] parts = updatedLine.Split(':');
                            if (parts.Length > 2 && int.TryParse(parts[0].Trim(), out int partNum) && int.TryParse(parts[2].Trim(), out int number))
                            {
                                loadnopicture(partNum.ToString(), number.ToString());
                                hasData = true;
                            }
                        }

                        // 2. โหลดรูปภาพหมายเลขสุดท้ายใน pictureBox37 และ pictureBox39 (เฉพาะเมื่อมีข้อมูล)
                        if (lines.Count > 0)
                        {
                            string lastLine = lines[lines.Count - 1];
                            string[] parts = lastLine.Split(':');

                            /*if (parts.Length > 2 && int.TryParse(parts[2].Trim(), out int lastNumber))
                            {
                                string lastImagePath = GetFullPath(lastNumber.ToString() + ".png");
                                //string lastImagePath39 = GetFullPath($"a{lastNumber}.png");

                                //if (pictureBox37 != null)
                                {
                                    pictureBox37.Image = LoadImageSafe(lastImagePath);
                                }
                                //if (pictureBox39 != null)
                                //{
                                //    pictureBox39.Image = LoadImageSafe(lastImagePath39); // โหลด aN.png
                                //}
                            }*/
                        }

                        if (!hasData)
                        {
                            if (btnRandom != null) btnRandom.Enabled = true;
                            if (btnReset != null) btnReset.Enabled = false;
                            if (btnResetall != null) btnResetall.Enabled = false;
                        }
                        else
                        {
                            if (btnRandom != null) btnRandom.Enabled = true;
                            if (btnReset != null) btnReset.Enabled = true;
                            if (btnResetall != null) btnResetall.Enabled = true;
                        }

                        MessageBox.Show("ลบรายการและแก้ไขลำดับเรียบร้อยแล้ว", "สำเร็จ", MessageBoxButtons.OK, MessageBoxIcon.Information);
                    }
                    else
                    {
                        MessageBox.Show("ไม่พบรายการที่ตรงกันในไฟล์", "ข้อผิดพลาด", MessageBoxButtons.OK, MessageBoxIcon.Error);
                    }
                }
                catch (Exception ex)
                {
                    MessageBox.Show($"เกิดข้อผิดพลาดในการทำงานกับไฟล์: {ex.Message}", "ข้อผิดพลาด", MessageBoxButtons.OK, MessageBoxIcon.Error);
                }
            }
        }

        private async Task SpinWheelAnimation(int targetNumber)
        {
            string spinningPath = GetFullPath("c.png");
            string targetImagePath = GetFullPath(targetNumber.ToString() + ".png");

            System.Drawing.Image loadedImage = LoadImageSafe(spinningPath);
            if (loadedImage == null) return;

            Bitmap originalBitmap = new Bitmap(loadedImage);
            loadedImage.Dispose();

            float targetAngle = (targetNumber - 1) * 10;
            float angleToStopAtTop = (360 - (targetAngle % 360)) % 360;
            float currentAngle = 0;
            int spinTime = 0;

            // 1. Fast Spin Phase (หมุนเร็ว)
            while (spinTime < 600)
            {
                currentAngle += 45;
                if (pictureBox37 != null) pictureBox37.Image = RotateImage(originalBitmap, currentAngle);

                // โหลดรูปสุ่ม aN.png ใน pictureBox39
                int randNumFor39 = rand.Next(1, TotalNumbers + 1);
                string tempImagePath39 = GetFullPath($"a{randNumFor39}.png");
                if (pictureBox39 != null)
                {
                    pictureBox39.Image = LoadImageSafe(tempImagePath39);
                    pictureBox39.Refresh();
                }

                if (player != null) player.Play();

                if (pictureBox37 != null) pictureBox37.Refresh();

                await Task.Delay(50);
                spinTime += 50;
            }

            // 2. Slow Spin Phase (หมุนช้าลงจนหยุด)
            float totalRotation = 360 * 1 + angleToStopAtTop;
            float angleToSpin = totalRotation - (currentAngle % 360);

            int rotationDelay = 50;
            float delayIncreasePerDegree = 0.5f;
            while (angleToSpin > 0)
            {
                float rotationStep;
                if (angleToSpin < 10) rotationStep = 2;
                else if (angleToSpin < 30) rotationStep = 5;
                else if (angleToSpin < 90) rotationStep = 10;
                else if (angleToSpin < 180) rotationStep = 20;
                else rotationStep = 40;
                if (rotationStep > angleToSpin) rotationStep = angleToSpin;
                if (player != null) player.Play();
                currentAngle += rotationStep;
                if (pictureBox37 != null) pictureBox37.Image = RotateImage(originalBitmap, currentAngle);

                // โหลดรูปสุ่ม aN.png ใน pictureBox39
                int randNumFor39 = rand.Next(1, TotalNumbers + 1);
                string tempImagePath39 = GetFullPath($"a{randNumFor39}.png");
                if (pictureBox39 != null)
                {
                    pictureBox39.Image = LoadImageSafe(tempImagePath39);
                    pictureBox39.Refresh();
                }

                if (pictureBox37 != null) pictureBox37.Refresh();

                await Task.Delay(rotationDelay);
                rotationDelay += (int)(rotationStep * delayIncreasePerDegree);
                if (rotationDelay > 500) rotationDelay = 500;

                angleToSpin -= rotationStep;
            }

            // 3. Final Reveal (ตั้งค่าภาพสุดท้าย)
            if (pictureBox37 != null) pictureBox37.Image = RotateImage(originalBitmap, currentAngle);
            if (pictureBox37 != null) pictureBox37.Refresh();

            // โหลด N.png ใน pictureBox37
            System.Drawing.Image finalImage = LoadImageSafe(targetImagePath);
            if (pictureBox37 != null && finalImage != null)
            {
                pictureBox37.Image = finalImage;
            }

            // โหลด aN.png ของเลขที่ออกใน pictureBox39
            string finalImagePath39 = GetFullPath($"a{targetNumber}.png");
            System.Drawing.Image finalImage39 = LoadImageSafe(finalImagePath39);
            if (pictureBox39 != null && finalImage39 != null)
            {
                pictureBox39.Image = finalImage39;
            }

            if (pictureBox37 != null) pictureBox37.Refresh();
            if (pictureBox39 != null) pictureBox39.Refresh();
            originalBitmap.Dispose();
        }

     

        #endregion

    }
}