/* ── HCLV SMS – main client JS ── */

// ── flash message auto-dismiss ──
document.querySelectorAll('.alert').forEach(el => {
  setTimeout(() => { el.style.transition = 'opacity .5s'; el.style.opacity = '0'; setTimeout(() => el.remove(), 500); }, 5000);
});

// ── confirm delete ──
document.querySelectorAll('[data-confirm]').forEach(btn => {
  btn.addEventListener('click', e => {
    if (!confirm(btn.dataset.confirm || 'Are you sure?')) e.preventDefault();
  });
});

// ── modal helpers ──
function openModal(id) {
  document.getElementById(id).classList.add('open');
  document.body.style.overflow = 'hidden';
}
function closeModal(id) {
  document.getElementById(id).classList.remove('open');
  document.body.style.overflow = '';
}
document.querySelectorAll('[data-modal-open]').forEach(btn => {
  btn.addEventListener('click', () => openModal(btn.dataset.modalOpen));
});
document.querySelectorAll('[data-modal-close]').forEach(btn => {
  btn.addEventListener('click', () => closeModal(btn.dataset.modalClose));
});
document.querySelectorAll('.modal-overlay').forEach(overlay => {
  overlay.addEventListener('click', e => {
    if (e.target === overlay) closeModal(overlay.id);
  });
});
document.querySelectorAll('[data-edit-subject]').forEach(btn => {
  btn.addEventListener('click', () => {
    document.getElementById('edit-subject-form').action = btn.dataset.action;
    document.getElementById('edit-subject-name').value = btn.dataset.name || '';
    document.getElementById('edit-subject-code').value = btn.dataset.code || '';
    document.getElementById('edit-subject-level').value = btn.dataset.level || 'O';
    openModal(btn.dataset.modalId);
  });
});

// ── sidebar toggle (mobile) ──
const sidebarToggle = document.getElementById('sidebar-toggle');
const sidebar = document.querySelector('.sidebar');
if (sidebarToggle && sidebar) {
  sidebarToggle.addEventListener('click', () => sidebar.classList.toggle('open'));
}

// ── calendar ──
const CalendarWidget = {
  year: new Date().getFullYear(),
  month: new Date().getMonth() + 1,
  events: [],

  async loadEvents() {
    try {
      const r = await fetch(`/admin/events?month=${this.month}&year=${this.year}`);
      this.events = await r.json();
    } catch (e) { this.events = []; }
    this.render();
  },

  render() {
    const container = document.getElementById('calendar-widget');
    if (!container) return;

    const daysInMonth = new Date(this.year, this.month, 0).getDate();
    const firstDay    = new Date(this.year, this.month - 1, 1).getDay();
    const today       = new Date();
    const eventDates  = new Set(this.events.map(e => e.event_date));
    const monthNames  = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const dayNames    = ['Su','Mo','Tu','We','Th','Fr','Sa'];

    let html = `<div class="d-flex align-items-center justify-content-between mb-2">
      <button class="btn btn-sm btn-outline" onclick="CalendarWidget.prev()">&#8249;</button>
      <strong style="color:var(--navy)">${monthNames[this.month-1]} ${this.year}</strong>
      <button class="btn btn-sm btn-outline" onclick="CalendarWidget.next()">&#8250;</button>
    </div>
    <div class="mini-cal"><table>
    <tr>${dayNames.map(d => `<th>${d}</th>`).join('')}</tr>`;

    let day = 1;
    for (let row = 0; row < 6 && day <= daysInMonth; row++) {
      html += '<tr>';
      for (let col = 0; col < 7; col++) {
        if ((row === 0 && col < firstDay) || day > daysInMonth) {
          html += '<td></td>';
        } else {
          const d = String(this.year) + '-' + String(this.month).padStart(2,'0') + '-' + String(day).padStart(2,'0');
          const isToday = today.getFullYear()===this.year && today.getMonth()+1===this.month && today.getDate()===day;
          const hasEv   = eventDates.has(d);
          const cls     = [isToday?'today':'', hasEv?'has-event':''].filter(Boolean).join(' ');
          html += `<td class="${cls}" onclick="CalendarWidget.dayClick('${d}')">${day}</td>`;
          day++;
        }
      }
      html += '</tr>';
    }
    html += '</table></div>';
    container.innerHTML = html;

    // render event list
    const list = document.getElementById('event-list');
    if (list) {
      if (this.events.length === 0) {
        list.innerHTML = '<p class="text-muted small text-center" style="padding:12px">No events this month</p>';
      } else {
        list.innerHTML = this.events.map(e => `
          <div class="event-item">
            <span class="event-dot"></span>
            <div>
              <div class="event-title">${escHtml(e.title)}</div>
              <div class="event-date">${e.event_date}${e.description ? ' – ' + escHtml(e.description) : ''}</div>
            </div>
            <button class="btn btn-xs btn-outline ms-auto" onclick="CalendarWidget.editEvent(${e.id})">✏️</button>
            <button class="btn btn-xs btn-danger" onclick="CalendarWidget.deleteEvent(${e.id})">✕</button>
          </div>`).join('');
      }
    }
  },

  prev() { this.month--; if(this.month<1){this.month=12;this.year--;} this.loadEvents(); },
  next() { this.month++; if(this.month>12){this.month=1;this.year++;} this.loadEvents(); },

  dayClick(d) {
    const modal = document.getElementById('event-modal');
    if (!modal) return;
    document.getElementById('ev-date').value = d;
    document.getElementById('ev-id').value   = '';
    document.getElementById('ev-title').value = '';
    document.getElementById('ev-desc').value  = '';
    document.getElementById('ev-type').value  = 'general';
    openModal('event-modal');
  },

  async saveEvent() {
    const id    = document.getElementById('ev-id').value;
    const title = document.getElementById('ev-title').value.trim();
    const edate = document.getElementById('ev-date').value;
    const desc  = document.getElementById('ev-desc').value.trim();
    const etype = document.getElementById('ev-type').value;
    if (!title || !edate) { alert('Title and date are required.'); return; }

    const body = JSON.stringify({ title, event_date: edate, description: desc, event_type: etype });
    const url  = id ? `/admin/events/${id}` : '/admin/events/add';
    const method = id ? 'PUT' : 'POST';

    await fetch(url, { method, headers: {'Content-Type':'application/json'}, body });
    closeModal('event-modal');
    this.loadEvents();
  },

  editEvent(id) {
    const ev = this.events.find(e => e.id === id);
    if (!ev) return;
    document.getElementById('ev-id').value    = ev.id;
    document.getElementById('ev-date').value  = ev.event_date;
    document.getElementById('ev-title').value = ev.title;
    document.getElementById('ev-desc').value  = ev.description || '';
    document.getElementById('ev-type').value  = ev.event_type || 'general';
    openModal('event-modal');
  },

  async deleteEvent(id) {
    if (!confirm('Delete this event?')) return;
    await fetch(`/admin/events/${id}`, { method: 'DELETE' });
    this.loadEvents();
  }
};

function escHtml(str) {
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

if (document.getElementById('calendar-widget')) CalendarWidget.loadEvents();

// ── marks: compute running grade ──
document.querySelectorAll('.score-input').forEach(inp => {
  inp.addEventListener('input', updateGrade);
});

function updateGrade(e) {
  const row = e.target.closest('tr');
  if (!row) return;
  const bots = row.querySelectorAll('.score-input[data-type=BOT]');
  const mts  = row.querySelectorAll('.score-input[data-type=MT]');
  const eots = row.querySelectorAll('.score-input[data-type=EOT]');
  let bot = bots[0] ? parseFloat(bots[0].value) : NaN;
  let mt  = mts[0]  ? parseFloat(mts[0].value)  : NaN;
  let eot = eots[0] ? parseFloat(eots[0].value) : NaN;

  let mot, final;
  const valid = v => !isNaN(v) && v >= 0;
  if (valid(bot) && valid(mt))      mot = (bot + mt) / 2;
  else if (valid(bot))              mot = bot;
  else if (valid(mt))               mot = mt;

  if (mot !== undefined && valid(eot)) final = mot * 0.5 + eot * 0.5;
  else if (mot !== undefined)          final = mot;
  else if (valid(eot))                 final = eot;

  const gradeEl = row.querySelector('.live-grade');
  if (gradeEl && final !== undefined) {
    const [g] = getGrade(final);
    gradeEl.textContent = g;
    gradeEl.className = 'live-grade badge grade-' + g;
  }
}

function getGrade(s) {
  if (s >= 80) return ['A','EXCEPTIONAL ACHIEVEMENT'];
  if (s >= 70) return ['B','OUTSTANDING PERFORMANCE'];
  if (s >= 60) return ['C','SATISFACTORY PERFORMANCE'];
  if (s >= 50) return ['D','BASIC UNDERSTANDING'];
  if (s >= 0)  return ['E','ELEMENTARY UNDERSTANDING'];
  return ['O','FAIL'];
}

// ── avatar preview ──
const avatarInput = document.getElementById('avatar-input');
if (avatarInput) {
  avatarInput.addEventListener('change', () => {
    const file = avatarInput.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = e => {
      const preview = document.getElementById('avatar-preview');
      if (preview) preview.src = e.target.result;
    };
    reader.readAsDataURL(file);
  });
}
