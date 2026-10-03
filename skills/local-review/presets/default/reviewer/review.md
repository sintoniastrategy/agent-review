Perform a comprehensive code review with the following focus areas:

1. **Code Quality**
   - Clean code principles and best practices
   - Proper error handling and edge cases
   - Code readability and maintainability
2. **Security**
   - Check for potential security vulnerabilities
   - Validate input sanitization
   - Review authentication/authorization logic
3. **Performance**
   - Identify potential performance bottlenecks
   - Review database queries for efficiency
   - Check for memory leaks or resource issues
4. **Testing**
   - Verify adequate test coverage
   - Review test quality and edge cases
   - Check for missing test scenarios
5. **Documentation**
   - Ensure code is properly documented
   - Verify README updates for new features
   - Check API documentation accuracy
6. **Guidelines Compliance**
   - Validate against `docs/go-guidelines.md` (error handling, logging, naming, testing, security, etc.) when present.
   - Validate against `docs/api-guidelines.md` (URL conventions, response format, status codes, pagination) when present.

Provide detailed feedback using inline comments for specific issues.
Use top-level comments for general observations or praise.
IMPORTANT: Always check for and follow the repository's CLAUDE.md file(s) as they contain repo-specific instructions and guidelines that must be followed.

Review the supplied fixed source snapshot using the selected scope and primary diff. Read changed files and surrounding code needed to support your claims. Use the actual base and merge base supplied by the helper. Preserve findings about documentation, tests, performance and maintainability even when they are not regressions; explain pre-existing behavior and scope concerns so the author and human can decide. State the severity, concrete trigger, impact and evidence. Distinguish an observed defect from an assumption or an unsupported case. Do not repeat a settled decision without new evidence or changed circumstances. Link related new findings with Related-To.
