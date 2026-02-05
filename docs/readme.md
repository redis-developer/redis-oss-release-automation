# Important Note

If you are new to behaviour trees, it is highly recommended to read some chapters from this book first:

[**Behavior Trees in Robotics and AI**](https://arxiv.org/abs/1709.00084) by Michele Colledanchise and Petter Ögren

- Chapters 1.1–1.5: Must read
- Chapter 2: Optional but recommended
- Chapter 3 (especially 3.5 "Creating Deliberative BTs using Backchaining"): Essential for understanding how the tree is built

Another source of inspiration is the py_trees documentation and examples:
https://py-trees.readthedocs.io/en/devel/index.html

## Visualization

Always use visualization commands (`release-print`, `conversation-print`) to explore the tree. This helps debug existing flows and design new ones. In DEBUG log level, the tree is printed in ASCII mode each tick, including all node states and feedback messages (not available in `release-print` graphical mode). Use `--log-file` and `--log-file-level` to output debug logs to a file (color will be lost).

To visualize only one package branch, combine `release-print` with the `--only-packages` option.

## Tree

The release tree is built so that the final status of the root node determines the success or failure of the entire release process. The same applies to each package branch—the top-level node status represents whether the package goal was achieved. However, internal nodes may return failure as part of normal control flow (e.g., a condition check returning false), which doesn't indicate an actual error.

Tree ticking relies on asyncio tasks: it ticks when tasks are ready and continues until none remain. This is efficient, but since async tasks are examined globally, combining tree execution with other async tasks is difficult. That's why the release process is sometimes called in a separate thread.

## Displaying Status

The display process models each package as a sequence of steps with result variables in the state, reflecting the high-level logic of the tree. Each package branch has a goal, and the tree tries to reach it by sequentially executing behaviours. To display meaningful status, we consider only steps with stateful results like workflow IDs, branch names, or artifacts. More details about steps and their linked state variables can be found in comments in `state.py`.