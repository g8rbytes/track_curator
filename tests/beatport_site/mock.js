// A stand-in for beatport.com's cart controls, shaped like the real page (header cart list,
// Cart Preferences, split buy button with a down arrow and a list of carts with checkboxes).
// The variant (A, B, C, E) changes how the list is built; see track.html.
const KEY = 'tcmock';
function fresh() {
  return {carts: [{key: 'cart', name: 'Main Cart', items: []}, {key: 'hold-bin', name: 'Hold Bin', items: []}],
          def: 'cart', variant: 'A', buyClicks: 0, caretClicks: 0};
}
function load() { return JSON.parse(localStorage.getItem(KEY) || 'null') || fresh(); }
function save(s) { localStorage.setItem(KEY, JSON.stringify(s)); }
let st = load();
function header() {
  const h = document.createElement('header');
  h.innerHTML = '<input type="text" placeholder="Search Beatport" name="q" style="width:300px"> <span class="nav"></span>';
  document.body.prepend(h);
  renderHeader();
}
function renderHeader() {
  const nav = document.querySelector('header .nav');
  const main = st.carts.find(c => c.key === 'cart');
  nav.innerHTML = `<a data-testid="header-mainnav-link-cart" aria-label="Open cart, ${main.items.length} items" href="#">${main.items.length}</a>`
    + `<div class="cartmenu" style="display:none"><a aria-label="Cart preferences" href="carts.html"><svg data-testid="icon-settings" role="img"></svg></a>`
    + st.carts.map(c => `<button type="button" aria-label="Open ${c.name}, ${c.items.length} items"><svg data-testid="cart-${c.key}" role="img"></svg></button>`).join('')
    + `</div><button type="button" aria-label="Open cart menu" data-testid="header-mainnav-link-cart">${main.items.length}</button>`;
}
