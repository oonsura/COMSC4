using System;
using System.Windows.Forms;

// 💡 สำคัญ: ต้องอยู่ใน Namespace เดียวกับ Form1 และ PasswordForm.Designer.cs
namespace Bingo
{
    public partial class PasswordForm : Form
    {
        public PasswordForm()
        {
            InitializeComponent();
            this.AcceptButton = btnOK;
            this.CancelButton = btnCancel;
        }

        public string Password
        {
            get { return txtPassword.Text; }
        }


        private void btnOK_Click(object sender, EventArgs e)
        {
            this.DialogResult = DialogResult.OK;
            this.Close();
        }

        private void btnCancel_Click(object sender, EventArgs e)
        {
            this.DialogResult = DialogResult.Cancel;
            this.Close();
        }

        private void PasswordForm_Load(object sender, EventArgs e)
        {
        }
    }
}