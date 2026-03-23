## Serenecode (Strict Mode)

All code in this project MUST follow the standards defined in SERENECODE.md. Read SERENECODE.md before writing or modifying any code. Every function — public and private — must have icontract preconditions and postconditions. Every class must have invariants. No exemptions.

### Verification

After each work iteration (implementing a feature, fixing a bug, refactoring), you MUST run verification before considering the task complete. Do not skip this.

**Structural + type check (seconds):**
```bash
serenecode check src/ --level 2
```

**Full verification with property testing (recommended):**
```bash
serenecode check src/ --level 3 --allow-code-execution
```

**Symbolic verification for critical functions (minutes):**
```bash
serenecode check src/core/ --level 4 --allow-code-execution
```

**Generate an HTML report:**
```bash
serenecode report src/ --format html --output report.html --allow-code-execution
```

Levels 3-5 import and execute project modules. Only use `--allow-code-execution` for trusted code.

If verification fails, read the error messages and fix the issues. Each failure includes the function name, file, line number, and a suggested fix. Iterate until all checks pass. Do not commit code that fails verification.
