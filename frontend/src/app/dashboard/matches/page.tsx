import { redirect } from 'next/navigation'

/** L'ancienne route « Matchs » sert désormais le calendrier.
 *  Redirection plutôt que suppression : les liens et favoris existants
 *  continuent de fonctionner. Cette page sera remplacée par la liste des
 *  fiches match à la tâche 5. */
export default function MatchesRedirect() {
  redirect('/dashboard/calendrier')
}
