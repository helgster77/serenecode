## Serenecode

All code in this project MUST follow the standards defined in SERENECODE.md. Read SERENECODE.md before writing or modifying any code. Every public function must have icontract preconditions and postconditions. Every class must have invariants. Follow the architectural patterns specified in SERENECODE.md.

### Verification

After each work iteration (implementing a feature, fixing a bug, refactoring), offer to run verification before considering the task complete.

**Quick structural check (seconds):**
```bash
serenecode check src/ --structural
```

**Full verification with property testing (minutes):**
```bash
serenecode check src/ --level 3
```

**Full verification including symbolic and compositional (minutes):**
```bash
serenecode check src/ --level 5
```

**Generate an HTML report:**
```bash
serenecode report src/ --format html --output report.html
```

If verification fails, read the error messages and fix the issues. Each failure includes the function name, file, line number, and a suggested fix. Iterate until all checks pass.
