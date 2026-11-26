# Stake Clone - Frontend UI

A responsive dark-mode glass UI for a Stake-inspired online gaming platform.

## Features

- **Responsive Lobby & Game Catalog**: Fast navigation between games, wallet, and profile.
- **Interactive Games**:
  - **Crash**: Rocket trajectory canvas, multiplier graph, auto-pilot controls, and live bet feeds.
  - **Live Blackjack**: 5-seat multiplayer card table layout with chip wagering and hand actions.
  - **Roulette**: European roulette betting grid, wheel simulation, and chip selection.
  - **Mines**: Customizable grid minesweeper game with cashout multipliers.
  - **Plinko**: Physics-style peg board with multi-row multipliers.
  - **Hi-Lo & Dice**: Dynamic probability slider and cards.
- **Glassmorphic Dark Theme**: Modern Stake-style UI with glowing accents, animated balances, sound toggles, and modal dialogues.
- **Account & Wallet Screens**: Login/Signup modal, avatar selector, transaction history, and wallet management.

## Tech Stack

- **HTML5 & Vanilla JavaScript**: Zero runtime framework dependencies, lightweight and fast.
- **CSS3 Modern Styles**: Glassmorphism, CSS variables, flexbox, and CSS grid.
- **SVG Vector Icons**: Inline resolution-independent game and UI iconography (`icons.js`).

## Running Locally

Simply open `index.html` in any web browser, or serve it using a local static file server:

```bash
# Using Python
python -m http.server 8000

# Using Node.js npx
npx serve .
```

Then visit `http://localhost:8000`.

## License
MIT License
