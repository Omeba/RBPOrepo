const target = document.getElementById('countdown');
if (target) {
  const date = Date.parse(target.dateTime);
  const update = () => {
    const seconds = Math.max(0, Math.floor((date - Date.now()) / 1000));
    target.textContent = seconds === 0 ? 'Событие наступило' :
      `${Math.floor(seconds / 86400)} д. ${Math.floor(seconds / 3600) % 24} ч. ${Math.floor(seconds / 60) % 60} мин. ${seconds % 60} с.`;
  };
  update();
  setInterval(update, 1000);
}
