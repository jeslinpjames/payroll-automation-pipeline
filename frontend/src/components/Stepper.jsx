const STEPS = ['Employees', 'Salary Sheet', 'Generate & Send']

export default function Stepper({ current }) {
  return (
    <nav className="stepper" aria-label="Progress">
      {STEPS.map((label, i) => {
        const state =
          i < current ? 'done' : i === current ? 'active' : 'upcoming'
        return (
          <div key={label} className={`step step--${state}`}>
            <span className="step__index">{i < current ? '✓' : i + 1}</span>
            <span className="step__label">{label}</span>
          </div>
        )
      })}
    </nav>
  )
}