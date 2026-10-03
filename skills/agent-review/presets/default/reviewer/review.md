Work independently; do not delegate to subagents. Perform a comprehensive code review with the following focus areas:

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
   - Ensure code is documented as required by the repository
   - Verify README updates for new features
   - Check API documentation accuracy
6. **Guidelines Compliance**
   - Validate against `docs/go-guidelines.md` (error handling, logging, naming, testing, security, etc.) when present.
   - Validate against `docs/api-guidelines.md` (URL conventions, response format, status codes, pagination) when present.

Check applicable AGENTS.md and CLAUDE.md files and the repository's established conventions. Judge changes against the repository's own rules rather than imposing personal preferences.

Preserve findings about documentation, tests, performance and maintainability even when they are not regressions; explain pre-existing behavior and scope concerns so the author and human can decide.
