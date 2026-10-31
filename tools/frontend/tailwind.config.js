// Design tokens live in src/web/static/style.css (:root / [data-theme="light"]) as "R G B" triplets so
// Tailwind opacity modifiers keep working (bg-s1/90). Change a theme there, not in the markup.
const token = (name) => `rgb(var(--${name}) / <alpha-value>)`;

module.exports = {
  darkMode: ['class', '[data-theme="dark"]'],
  content: [
    '../../src/web/templates/**/*.html',
    '../../src/web/static/js/**/*.js',
  ],
  theme: {
    extend: {
      colors: {
        app: token('app'),          // window background
        s1: token('s1'),            // cards / panels
        s2: token('s2'),            // inputs, raised rows
        s3: token('s3'),            // buttons
        s4: token('s4'),            // button hover / strong fills
        hv: token('hv'),            // row hover
        line: token('line'),        // hairlines
        line2: token('line2'),      // input / control borders
        fg: token('fg'),            // primary text
        fg2: token('fg2'),
        fg3: token('fg3'),          // secondary text
        fg4: token('fg4'),          // tertiary text (still >= 4.5:1)
        inv: token('inv'),          // inverse pill background
        'inv-fg': token('inv-fg'),
        spotify: {
          green: '#1DB954',
          hover: '#1ed760',
          dark: '#121212',
          card: '#181818',
          cardHover: '#242424',
          subtext: '#a0a0a0',
          border: '#2a2a2a',
        },
      },
      fontFamily: {
        sans: ['"Plus Jakarta Sans"', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'monospace'],
      },
      // 12px floor: nothing in the UI is smaller than 2xs.
      fontSize: {
        '2xs': ['0.75rem', { lineHeight: '1.05rem' }],
        xs: ['0.8125rem', { lineHeight: '1.2rem' }],
        sm: ['0.9375rem', { lineHeight: '1.4rem' }],
      },
    },
  },
};
