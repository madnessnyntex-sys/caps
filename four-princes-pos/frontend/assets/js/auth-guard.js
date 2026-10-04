(function () {
  const token = Api.getToken();
  const user = Api.getUser();
  if (!token || !user) {
    window.location.href = 'login.php';
    return;
  }

  document.addEventListener('DOMContentLoaded', function () {
    if (user.role !== 'admin') {
      document.querySelectorAll('[data-admin-only]').forEach(el => { el.style.display = 'none'; });
    }
    document.querySelectorAll('[data-user-name]').forEach(el => { el.textContent = user.full_name; });
    document.querySelectorAll('[data-user-role]').forEach(el => {
      el.textContent = user.role === 'admin' ? 'Administrator' : 'Staff';
    });
    document.querySelectorAll('[data-user-initial]').forEach(el => {
      el.textContent = user.full_name.charAt(0).toUpperCase();
    });

    const logoutBtn = document.getElementById('logoutBtn');
    if (logoutBtn) {
      logoutBtn.addEventListener('click', function () {
        Api.clearSession();
        window.location.href = 'login.php';
      });
    }

    const changePasswordBtn = document.getElementById('changePasswordBtn');
    const changePasswordForm = document.getElementById('changePasswordForm');
    if (changePasswordBtn && changePasswordForm) {
      changePasswordBtn.addEventListener('click', function () {
        changePasswordForm.reset();
        document.getElementById('changePasswordError').hidden = true;
        document.getElementById('changePasswordSuccess').hidden = true;
        openModal('changePasswordModal');
      });

      changePasswordForm.addEventListener('submit', async function (e) {
        e.preventDefault();
        const errorBox = document.getElementById('changePasswordError');
        const successBox = document.getElementById('changePasswordSuccess');
        errorBox.hidden = true;
        successBox.hidden = true;
        const submitBtn = changePasswordForm.querySelector('button[type="submit"]');
        submitBtn.disabled = true;
        try {
          await Api.patch('/auth/me/password', {
            current_password: document.getElementById('currentPassword').value,
            new_password: document.getElementById('newPassword').value,
          });
          successBox.hidden = false;
          changePasswordForm.reset();
        } catch (err) {
          errorBox.textContent = err.message;
          errorBox.hidden = false;
        } finally {
          submitBtn.disabled = false;
        }
      });
    }
  });
})();
