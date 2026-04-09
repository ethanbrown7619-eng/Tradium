/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{js,jsx,ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#f0f5ff",
          100: "#e0eaff",
          500: "#4f6ef7",
          600: "#3b5ce4",
          700: "#2d4bd1",
          900: "#1a2f8a",
        },
        profit: "#22c55e",
        loss: "#ef4444",
      },
    },
  },
  plugins: [],
};
