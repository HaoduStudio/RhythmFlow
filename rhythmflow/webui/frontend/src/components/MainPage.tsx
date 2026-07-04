import { AlignmentTable } from './AlignmentTable';
import { InputsCard } from './InputsCard';
import { OptionsCard } from './OptionsCard';
import { ProgressLog } from './ProgressLog';

export function MainPage(): JSX.Element {
  return (
    <>
      <div className="cards-grid">
        <InputsCard />
        <OptionsCard />
      </div>
      <AlignmentTable />
      <ProgressLog />
    </>
  );
}
