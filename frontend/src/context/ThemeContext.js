import { createContext, useContext } from "react";

const ThemeContext = createContext({ theme: "dark" });

export function ThemeProvider({ children }) {
  return <ThemeContext.Provider value={{ theme: "dark" }}>{children}</ThemeContext.Provider>;
}

export const useTheme = () => useContext(ThemeContext);
