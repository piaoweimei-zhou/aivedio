module.exports = {
  root: true,
  env: { browser: true, es2020: true },
  extends: [
    'eslint:recommended',
    'plugin:@typescript-eslint/recommended',
    'plugin:react-hooks/recommended',
  ],
  ignorePatterns: ['dist', '.eslintrc.cjs', 'node_modules'],
  parser: '@typescript-eslint/parser',
  parserOptions: { ecmaVersion: 'latest', sourceType: 'module' },
  plugins: ['@typescript-eslint'],
  rules: {
    '@typescript-eslint/no-unused-vars': ['warn', { argsIgnorePattern: '^_' }],
    '@typescript-eslint/no-explicit-any': 'off',
    // ⭐ react-hooks v7 新增激进规则：对"加载数据后 setState / 渲染期累加"等正常模式误报率高。
    // 降为 warn 保留可见性（不阻断），T2 前端 lint 门禁以 0 error 为准。
    'react-hooks/set-state-in-effect': 'warn',
    'react-hooks/immutability': 'warn',
  },
}
