/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./lib/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        primary: {
          DEFAULT: "#385854",
          50: "#f0f7f6",
          100: "#d9edeb",
          200: "#b3dbd7",
          300: "#82c2bc",
          400: "#5aa39c",
          500: "#3f8a83",
          600: "#385854",
          700: "#2d4744",
          800: "#263a37",
          900: "#1e2e2c",
        },
      },
    },
  },
  plugins: [require("@tailwindcss/typography")],
};
