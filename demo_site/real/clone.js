(function () {
  var mn = document.querySelector('.gal .main');
  var th = Array.prototype.slice.call(document.querySelectorAll('.strip .th'));
  var cnt = document.querySelector('.gal .count');
  var cur = 0;
  function show(i) {
    if (!th.length || !mn) return;
    cur = (i + th.length) % th.length;
    th.forEach(function (x) { x.classList.remove('on'); });
    th[cur].classList.add('on');
    mn.src = th[cur].src;
    mn.alt = th[cur].alt;
    if (cnt) cnt.textContent = (cur + 1) + ' / ' + th.length;
  }
  th.forEach(function (t, i) { t.addEventListener('click', function () { show(i); }); });
  var prev = document.querySelector('.strip .arw.prev'), next = document.querySelector('.strip .arw.next');
  if (prev) prev.addEventListener('click', function () { show(cur - 1); });
  if (next) next.addEventListener('click', function () { show(cur + 1); });
  if (mn) mn.addEventListener('click', function () { show(cur + 1); });

  var m = document.querySelector('.gallery-modal');
  document.querySelectorAll('[data-gal]').forEach(function (b) {
    b.addEventListener('click', function (e) {
      e.preventDefault();
      if (!m) return;
      m.hidden = false;
      var i = parseInt(b.getAttribute('data-i') || '0', 10);
      var f = m.querySelectorAll('figure')[i];
      if (f) f.scrollIntoView();
    });
  });
  var c = m && m.querySelector('.close');
  if (c) c.addEventListener('click', function () { m.hidden = true; });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && m) m.hidden = true; });

  // tabs
  document.querySelectorAll('.tab').forEach(function (t) {
    t.addEventListener('click', function () {
      document.querySelectorAll('.tab').forEach(function (x) { x.classList.remove('on'); });
      document.querySelectorAll('.panel').forEach(function (x) { x.classList.remove('on'); });
      t.classList.add('on');
      var p = document.getElementById(t.getAttribute('data-t'));
      if (p) p.classList.add('on');
    });
  });

  // floor-plan filter (Houston)
  document.querySelectorAll('.fp-filter button').forEach(function (b) {
    b.addEventListener('click', function () {
      document.querySelectorAll('.fp-filter button').forEach(function (x) { x.classList.remove('on'); });
      b.classList.add('on');
      var f = b.getAttribute('data-f');
      document.querySelectorAll('.fp-card').forEach(function (card) {
        var bd = card.querySelector('.fp-beds').textContent;
        var k = /Studio/.test(bd) ? 'S' : bd.charAt(0);
        card.hidden = !(f === 'all' || f === k);
      });
    });
  });
})();
