(function () {
  document.querySelectorAll('[data-more]').forEach(function (b) {
    b.addEventListener('click', function () {
      var t = document.querySelector(b.dataset.more);
      if (t) { t.hidden = !t.hidden; b.textContent = t.hidden ? 'Read more' : 'Show less'; }
    });
  });
  var m = document.querySelector('.gallery-modal');
  document.querySelectorAll('[data-gal]').forEach(function (b) {
    b.addEventListener('click', function () { if (m) m.hidden = false; });
  });
  var c = m && m.querySelector('.close');
  if (c) c.addEventListener('click', function () { m.hidden = true; });
  document.querySelectorAll('.strip .th').forEach(function (t) {
    t.addEventListener('click', function () {
      document.querySelectorAll('.strip .th').forEach(function (x) { x.classList.remove('on'); });
      t.classList.add('on');
      var mn = document.querySelector('.gal .main');
      if (mn) mn.className = 'main ' + t.dataset.p;
    });
  });
})();
