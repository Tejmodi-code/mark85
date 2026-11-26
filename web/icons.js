/* Line icons on a 24px grid. Trusted static markup, never built from user input. */
const ICONS = {
  home: '<path d="M4 11l8-7 8 7v8a1 1 0 0 1-1 1h-4v-6H9v6H5a1 1 0 0 1-1-1z"/>',
  wallet: '<path d="M4 7.5A2.5 2.5 0 0 1 6.5 5H18v3"/><rect x="4" y="8" width="16" height="11" rx="2.5"/><circle cx="16" cy="13.5" r="1"/>',
  user: '<circle cx="12" cy="8.5" r="3.5"/><path d="M5 20c.6-3.6 3.4-5.5 7-5.5s6.4 1.9 7 5.5"/>',
  dice: '<rect x="4" y="4" width="16" height="16" rx="4"/><circle cx="9" cy="9" r=".6"/><circle cx="15" cy="9" r=".6"/><circle cx="12" cy="12" r=".6"/><circle cx="9" cy="15" r=".6"/><circle cx="15" cy="15" r=".6"/>',
  limbo: '<path d="M4 18L10 11l3 3 7-8"/><path d="M15 6h5v5"/>',
  flip: '<circle cx="12" cy="12" r="8"/><path d="M12 8v8M9.5 10c0-1 1-1.6 2.5-1.6s2.5.6 2.5 1.6-1 1.4-2.5 1.8-2.5.8-2.5 1.8 1 1.6 2.5 1.6 2.5-.6 2.5-1.6"/>',
  roulette: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="2.5"/><path d="M12 4v5.5M12 14.5V20M4 12h5.5M14.5 12H20"/>',
  bj: '<rect x="6" y="3.5" width="12" height="17" rx="2.5"/><path d="M12 9.5c-1.6-2-4-.5-2.7 1.6.6 1 2.7 2.4 2.7 2.4s2.1-1.4 2.7-2.4c1.3-2.1-1.1-3.6-2.7-1.6z"/>',
  crash: '<path d="M4 19h16"/><path d="M5 16c3 0 4-4 6-6s4-2 7-5"/><circle cx="18" cy="5" r="1.2"/>',
  mines: '<path d="M12 4l7 5-7 11L5 9z"/><path d="M5 9h14M9 9l3 11 3-11M9.5 9L12 4l2.5 5"/>',
  hilo: '<path d="M8 10l4-4 4 4"/><path d="M8 14l4 4 4-4"/>',
  plinko: '<circle cx="12" cy="5" r="1"/><circle cx="8.5" cy="10" r="1"/><circle cx="15.5" cy="10" r="1"/><circle cx="5" cy="15" r="1"/><circle cx="12" cy="15" r="1"/><circle cx="19" cy="15" r="1"/><path d="M4 20h16"/>',
  gift: '<rect x="4" y="9" width="16" height="11" rx="2"/><path d="M12 9v11M4 13h16M12 9c-2.5 0-4-1-4-2.5S9 4 10.5 5 12 9 12 9zm0 0c2.5 0 4-1 4-2.5S15 4 13.5 5 12 9 12 9z"/>',
  shield: '<path d="M12 3.5l7 2.5v5.5c0 4.3-3 7.6-7 9-4-1.4-7-4.7-7-9V6z"/><path d="M9 12l2.3 2.3L15.5 10"/>',
  menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  coin: '<circle cx="12" cy="12" r="8"/><path d="M12 8v8"/>',
  bomb: '<circle cx="11" cy="13.5" r="6"/><path d="M15 9l3-3M17.5 4.5l2 2"/>'
};
const icon = (name, size = 20) =>
  `<svg class="ico" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ''}</svg>`;
